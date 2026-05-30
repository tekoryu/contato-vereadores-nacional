# Async pipeline flow

How `src/pipeline_async.py` and `src/fetcher_async.py` work together.

## Top-level: who exists and who talks to whom

```
                     ┌──────────────────────────────────────┐
                     │            event loop                │
                     │  (single thread, dispatches tasks)   │
                     └──────────────────────────────────────┘
                                       │
                                       │ runs/parks/resumes
                                       ▼
   ┌─────────────┐     ┌──────────┐     ┌──────────┐     ┌──────────────┐
   │  producer   │     │ worker 0 │     │ worker 1 │     │   worker 2   │
   │ (run loop)  │     │          │     │          │     │              │
   └──────┬──────┘     └─────┬────┘     └─────┬────┘     └──────┬───────┘
          │  put              │ get            │ get             │ get
          ▼                   ▼                ▼                 ▼
        ┌────────────────────────────────────────────────────────────┐
        │                       in_q  (asyncio.Queue)                │
        │   politicians waiting to be processed (maxsize = N*2)      │
        └────────────────────────────────────────────────────────────┘

   workers 0, 1, 2 ─── put results ───►  ┌────────────────────────┐
                                          │   results_q (Queue)    │
                                          └──────────┬─────────────┘
                                                     │ get
                                                     ▼
                                             ┌───────────────┐
                                             │   writer      │
                                             │  (1 task)     │
                                             └───────┬───────┘
                                                     │ append line
                                                     ▼
                                             results.jsonl

   workers 0, 1, 2 ─── shared, mutated synchronously ───►  dead_urls dict
                                                            (dumped to disk
                                                             on each update —
                                                             safe because the
                                                             update has no
                                                             `await` inside)
```

Key fact: **all the boxes above exist at the same time**. The event loop just decides which one gets to run for the next instant.

## Lifecycle: what happens when you run the script

```
$ python src/pipeline_async.py --concurrency 3

   1. load_data()                  ← politicians JSON + IBGE→URL map
   2. load_processed_ids()         ← skip already-done politicians
   3. load_dead_urls()             ← cache of known-broken sites
   4. launch Chromium browser      (one process, shared)
   5. asyncio.create_task(writer)  ← parks on results_q.get()
   6. asyncio.create_task(worker)  ×3   ← all 3 park on in_q.get()
   7. for each politician: await in_q.put(...)
   8. for each worker: await in_q.put(None)   ← shutdown sentinels
   9. await all workers to finish
   10. await results_q.put(None)   ← shutdown sentinel for writer
   11. await writer
   12. close browser
```

## What one worker does, step by step

```
            ┌──────────────────────────────┐
            │  await in_q.get()            │ ◄─── parked until producer puts
            └──────────────┬───────────────┘
                           │ got a politician (or None=shutdown)
                           ▼
                   ┌───────────────┐
                   │ item is None? │──── yes ──► return (worker exits)
                   └───────┬───────┘
                           │ no
                           ▼
            ┌──────────────────────────────┐
            │ look up URLs in url_map      │
            └──────────────┬───────────────┘
                           │
                  no URLs ─┴─ has URLs
                     │              │
                     ▼              ▼
        write "no_url" to    ┌──────────────────────────┐
        results_q, loop ───► │ for each candidate URL:  │
                             │   skip if in dead_urls   │
                             │   await crawl_for_email  │ ◄── (see below)
                             │   on error: record_dead  │
                             └──────────┬───────────────┘
                                        │
                                        ▼
                       found / error / not_found
                                        │
                                        ▼
                          await results_q.put(record)
                                        │
                                        ▼
                              loop back to in_q.get()
```

## Inside `crawl_for_email` (one politician's crawl)

```
   start_url, depth=0
        │
        ▼
   ┌───────────────────────┐
   │ await fetch_page(url) │ ◄── pause point (network ~3.5s)
   └──────────┬────────────┘
              │ (text, links)
              ▼
   ┌───────────────────────┐
   │ extract_emails(text)  │ (sync, fast)
   └──────────┬────────────┘
              │
       found emails?
        │           │
       yes          no
        │           │
        ▼           │
   ┌─────────────────────────────┐
   │ await identify_email(...)   │ ◄── pause point (LLM ~0.5s)
   └──────────┬──────────────────┘
              │
        matched?─── yes ──► return (email, url)   ✅
              │
              no
              ▼
   depth == max_depth? ── yes ──► return None
              │ no
              ▼
   ┌─────────────────────────────┐
   │ filter links by domain      │ (sync)
   └──────────┬──────────────────┘
              ▼
   ┌─────────────────────────────┐
   │ await pick_best_link(...)   │ ◄── pause point (LLM ~0.9s)
   └──────────┬──────────────────┘
              │
        chose a link?── no ──► return None
              │ yes
              ▼
        current_url = next_href
        depth += 1
              │
              └──► back to fetch_page
```

Every `await` in this diagram is a place where the worker can step aside and let another worker run.

## Timeline: how 3 workers interleave on the wall clock

`█` = active CPU work / event loop attention. `·` = parked, waiting on I/O.

```
                t=0s    t=1s    t=2s    t=3s    t=4s    t=5s
worker 0    ██···········█···████···········█···████···
worker 1    ··██···········█···████···········█···████·
worker 2    ····██···········█···████···········█···██·
writer      ········█···········█···········█·········█
event loop  ████████████████████████████████████████████
                (always busy switching between whoever is ready)
```

What's happening:
- All three workers fetch a page in parallel (network handles it fine).
- When a fetch returns, that worker resumes briefly, hits an LLM call, parks again.
- LLM calls queue at the single Ollama/GPU — but each is short (~0.7s) so it's not a bottleneck.
- The writer wakes up briefly each time a worker puts a record on its queue.

## Why this is safe (no locks, no threads)

asyncio runs on **one thread**. Two coroutines can never be inside Python code at the same instant. Race conditions only happen when state mutation crosses an `await`. So:

- `results.jsonl` → only the writer task opens the file → impossible for two writes to interleave.
- `dead_urls` dict → mutated by `record_dead_url`, which has no `await` inside → the mutation + disk dump are atomic from the event loop's view.
- `in_q` / `results_q` → asyncio.Queue is designed for this; `get()` and `put()` are the synchronization.

## Tuning concurrency

The bottleneck shifts as you add workers:

| N | Bottleneck       | Expected speedup vs sync |
|---|------------------|--------------------------|
| 1 | sequential       | 1.0×                     |
| 2 | network          | ~1.9×                    |
| 3 | network          | ~2.9×                    |
| 4 | GPU starts to    | ~3.5×                    |
| 8 | GPU-bound        | ~4.7× (diminishing)      |

Past 4, you'd need a second Ollama instance or a smaller model to keep scaling.

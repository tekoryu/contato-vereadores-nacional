# Upwork Case Study Video Script: AI-Assisted Web Scraper

Use this script to record a **1.5 to 2-minute Loom or YouTube video** to showcase your project on Upwork. Video walkthroughs dramatically increase client response rates!

---

## Recording Tips:
* **Tone:** Confident, professional, and solution-oriented.
* **Format:** Screen share + a circular bubble camera of yourself in the corner.
* **Resolution:** Ensure code and terminal text are zoomed in and easy to read.

---

## Scene-by-Scene Script

### Scene 1: Introduction (The Hook)
* **What to show on screen:** Open [contato-vereadores-hero-refined.png](file:///Users/NullAxiom/Projects/contato-vereadores-nacional/portfolio/thumbnail/contato-vereadores-hero-refined.png) in full screen.
* **Spoken Script:**
  > "Hi there! If you've ever tried to scrape contact data from government websites, you know it's a nightmare. 
  > 
  > In this video, I want to show you how I built a highly resilient, AI-assisted web scraping pipeline that fully mapped all 5,571 Brazilian municipalities to extract politician emails with **$0 API cost**."

---

### Scene 2: The Core Problem (Dynamic & Random Sites)
* **What to show on screen:** Open a browser showing a random municipal government site (like a câmara municipal page), showing links like "Vereadores", "Contato", "Fale Conosco".
* **Spoken Script:**
  > "Traditional scrapers fail here because public municipal sites have zero standardized layouts. They are built on different platforms, load content dynamically via JavaScript, and change constantly.
  > 
  > To solve this, instead of writing hardcoded xpath rules for five thousand different sites, I built a scraper that navigates websites like a human agent."

---

### Scene 3: The Architecture
* **What to show on screen:** Bring back the 4-stage pipeline diagram in [contato-vereadores-hero-refined.png](file:///Users/NullAxiom/Projects/contato-vereadores-nacional/portfolio/thumbnail/contato-vereadores-hero-refined.png) and highlight the **AI Crawler** card.
* **Spoken Script:**
  > "Here is the architecture of the pipeline. We ingest raw rosters at the **Bronze** layer, validate base URLs at **Silver**, and then pass them to the **AI Crawler**.
  > 
  > This crawler uses **Playwright** to spin up headless Chromium instances to fetch dynamic content. Then, instead of sending page text to expensive APIs like OpenAI, it routes it to a local **Ollama** instance running a `Qwen2.5` model. 
  > 
  > The AI makes real-time navigation decisions: it looks at the page links, chooses which one is most likely to contain contact info, clicks it, and extracts the target email."

---

### Scene 4: Code & Live Demo
* **What to show on screen:** Open your editor showing [fetcher.py](file:///Users/NullAxiom/Projects/contato-vereadores-nacional/src/fetcher.py) and then pull up your terminal to run a quick test.
* **Spoken Script:**
  > "Let's look at the core crawler logic. In the python code here, we extract links on the page and ask the local LLM to pick the next target. 
  > 
  > If we run it in the terminal, you can see the crawler actively visiting the start page, finding the navigation links, and asking the LLM to identify the correct emails. If the model finds matches, it validates them and writes them directly to our final structured results database."

---

### Scene 5: Scalability & Checkpoint Resiliency
* **What to show on screen:** Show the `data/silver/results.jsonl` file or print some lines of it in the terminal to show structured data.
* **Spoken Script:**
  > "Because this pipeline runs on local hardware, we can scrape millions of pages without spending a single dollar on API tokens. 
  > 
  > To ensure the pipeline could run for days without data loss, I built checkpoint resiliency using Pandas and JSONL checkpoints. If the network drops or a site times out, the engine caches dead links and resumes exactly where it left off."

---

### Scene 6: Outro (Call to Action)
* **What to show on screen:** Go back to your camera bubble in full screen, or display your GitHub repository / Upwork profile.
* **Spoken Script:**
  > "If you need highly resilient scraping pipelines, custom browser automation, or locally-hosted AI integration to process unstructured data at scale, feel free to send me a message here on Upwork. Let's discuss how we can automate your data workflows!"

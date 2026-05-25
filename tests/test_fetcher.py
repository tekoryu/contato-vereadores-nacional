from unittest.mock import MagicMock, patch

import pytest

from fetcher import extract_emails, identify_email, pick_best_link


def make_ai_response(content: str) -> MagicMock:
    """Builds a fake ollama.chat() return value with the given text content."""
    response = MagicMock()
    response.message.content = content
    return response


# --- extract_emails ---

class TestExtractEmails:
    def test_finds_single_email(self):
        text = "Entre em contato: vereador@camara.mg.leg.br"
        assert extract_emails(text) == ["vereador@camara.mg.leg.br"]

    def test_finds_multiple_emails(self):
        text = "joao@camara.mg.leg.br ou contato@camara.mg.leg.br"
        assert set(extract_emails(text)) == {"joao@camara.mg.leg.br", "contato@camara.mg.leg.br"}

    def test_deduplicates(self):
        text = "mesmo@email.com e mesmo@email.com novamente"
        assert len(extract_emails(text)) == 1

    def test_returns_empty_for_no_emails(self):
        assert extract_emails("Nenhum email aqui.") == []

    def test_ignores_invalid_patterns(self):
        assert extract_emails("isso@não é um email") == []


# --- identify_email ---

class TestIdentifyEmail:
    def test_returns_email_when_ai_matches(self):
        emails = ["sidineia@mamonas.mg.leg.br", "contato@mamonas.mg.leg.br"]
        with patch("fetcher.ollama.chat", return_value=make_ai_response("sidineia@mamonas.mg.leg.br")):
            result = identify_email(emails, "Sidineia do Hospital", "test-model")
        assert result == "sidineia@mamonas.mg.leg.br"

    def test_returns_none_when_ai_says_none(self):
        emails = ["contato@mamonas.mg.leg.br"]
        with patch("fetcher.ollama.chat", return_value=make_ai_response("none")):
            result = identify_email(emails, "Sidineia do Hospital", "test-model")
        assert result is None

    def test_none_check_is_case_insensitive(self):
        emails = ["contato@mamonas.mg.leg.br"]
        with patch("fetcher.ollama.chat", return_value=make_ai_response("NONE")):
            result = identify_email(emails, "Sidineia do Hospital", "test-model")
        assert result is None

    def test_skips_ai_when_no_emails(self):
        with patch("fetcher.ollama.chat") as mock_chat:
            result = identify_email([], "Sidineia do Hospital", "test-model")
        assert result is None
        mock_chat.assert_not_called()


# --- pick_best_link ---

class TestPickBestLink:
    LINKS = [
        {"text": "Início", "href": "http://camara.mg.leg.br/"},
        {"text": "Vereadores", "href": "http://camara.mg.leg.br/vereadores"},
        {"text": "Contato", "href": "http://camara.mg.leg.br/contato"},
    ]

    def test_returns_href_for_valid_number(self):
        with patch("fetcher.ollama.chat", return_value=make_ai_response("2")):
            result = pick_best_link(self.LINKS, "Sidineia do Hospital", "test-model")
        assert result == "http://camara.mg.leg.br/vereadores"

    def test_returns_none_when_ai_says_zero(self):
        with patch("fetcher.ollama.chat", return_value=make_ai_response("0")):
            result = pick_best_link(self.LINKS, "Sidineia do Hospital", "test-model")
        assert result is None

    def test_returns_none_when_ai_returns_non_number(self):
        with patch("fetcher.ollama.chat", return_value=make_ai_response("não sei")):
            result = pick_best_link(self.LINKS, "Sidineia do Hospital", "test-model")
        assert result is None

    def test_skips_ai_when_no_links(self):
        with patch("fetcher.ollama.chat") as mock_chat:
            result = pick_best_link([], "Sidineia do Hospital", "test-model")
        assert result is None
        mock_chat.assert_not_called()

class AIReceptionist:
    """Stub for future LLM + voice integration."""

    def __init__(self):
        from app.services.lead_extractor import LeadExtractor
        self._extractor = LeadExtractor()

    def transcribe_call(self, audio_data=None):
        """Transcribe call audio to text."""
        pass

    def extract_lead_info(self, transcript=None, phone=None):
        """Extract structured lead fields from a call transcript."""
        return self._extractor.extract(transcript=transcript or "", phone=phone or "")

    def suggest_reply(self, context=None):
        """Suggest a reply for the AI receptionist."""
        pass

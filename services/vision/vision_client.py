"""
Vision-Analyse — mehrere Provider fuer Bild-Text-Anfragen.

Prioritaet:
  1. Groq Llama-4-Scout  (meta-llama/llama-4-scout-17b-16e-instruct) — existierender Groq-Key
  2. Google Gemini Flash  (gemini-1.5-flash)                          — kostenlos, beste Qualitaet
  3. Ollama (lokal)       (moondream / llava)                          — offline, kein Key noetig

Verwendung:
    analyzer = VisionAnalyzer(groq_api_key="gsk_...", gemini_api_key="AI...")
    result   = analyzer.analyze(image_b64, "Was siehst du?")
    result   = analyzer.analyze_video(frames_b64_list, "Was passiert hier?")
"""
import base64
import io
import json
import logging
import urllib.error
import urllib.request
from typing import List, Optional

logger = logging.getLogger("jarvis.vision")

_DEFAULT_QUESTION = (
    "Was siehst du auf diesem Bild? "
    "Beschreibe den Inhalt praezise und auf Deutsch in 2-4 Saetzen."
)


class VisionAnalyzer:
    # Prioritized list of Groq vision models to try (in order)
    _GROQ_VISION_MODELS = [
        "meta-llama/llama-4-scout-17b-16e-instruct",  # Llama 4 Scout — primary (2025+)
        "meta-llama/llama-4-maverick-17b-128e-instruct",  # Llama 4 Maverick — fallback
        "llama-3.2-90b-vision-preview",               # Legacy (may be decommissioned)
        "llama-3.2-11b-vision-preview",               # Legacy (decommissioned)
    ]

    def __init__(
        self,
        groq_api_key:  str = "",
        gemini_api_key: str = "",
        ollama_url:    str = "http://localhost:11434",
        ollama_model:  str = "moondream",
    ):
        self._groq_key     = groq_api_key
        self._gemini_key   = gemini_api_key
        self._ollama_url   = ollama_url.rstrip("/")
        self._ollama_model = ollama_model

    # ── Oeffentliche API ──────────────────────────────────────────────────────

    def analyze(
        self,
        image_b64: str,
        question:  str = _DEFAULT_QUESTION,
    ) -> str:
        """
        Analysiert ein Base64-JPEG mit der besten verfuegbaren Vision-KI.
        Probiert Provider in Prioritaetsreihenfolge; faellt auf den naechsten zurueck.
        """
        if not image_b64:
            return "Kein Bild vorhanden — Kamera-Aufnahme fehlgeschlagen."

        # Provider 1: Groq Vision
        if self._groq_key:
            try:
                result = self._analyze_groq(image_b64, question)
                if result and result.strip():
                    # BUG-076: log said "Llama-3.2" unconditionally; actual model is
                    # logged inside _analyze_groq() — keep outer message generic.
                    logger.info("Vision: Groq provider used.")
                    return result
            except Exception as e:
                logger.warning(f"Groq Vision Fehler: {e}")

        # Provider 2: Gemini Flash
        if self._gemini_key:
            try:
                result = self._analyze_gemini(image_b64, question)
                if result and result.strip():
                    logger.info("Vision: Google Gemini Flash verwendet.")
                    return result
            except Exception as e:
                logger.warning(f"Gemini Vision Fehler: {e}")

        # Provider 3: Ollama (lokal)
        try:
            result = self._analyze_ollama(image_b64, question)
            if result and result.strip():
                logger.info(f"Vision: Ollama ({self._ollama_model}) verwendet.")
                return result
        except Exception as e:
            logger.warning(f"Ollama Vision Fehler: {e}")

        return (
            "Kein Vision-Provider verfuegbar. "
            "Bitte Groq-API-Key, Gemini-Key oder Ollama pruefen."
        )

    def analyze_video(
        self,
        frames_b64: List[str],
        question:   str = "",
        grid_cols:  int = 3,
    ) -> str:
        """
        Analysiert eine kurze Videosequenz als Liste von Base64-JPEG-Frames.
        Die Frames werden zu einem Collage-Grid zusammengesetzt und als Einzelbild
        an die verfuegbaren Vision-Provider gesendet.

        frames_b64 : Liste von Base64-JPEG-Strings (z.B. 6–9 Frames à ~1s Abstand)
        question   : Analyse-Frage (Deutsch); wird automatisch gesetzt wenn leer
        grid_cols  : Anzahl Spalten im Grid (Standard: 3)
        """
        if not frames_b64:
            return "Keine Video-Frames vorhanden — Aufnahme fehlgeschlagen."

        if not question:
            question = (
                "Dies sind mehrere aufeinanderfolgende Frames aus einem kurzen Video. "
                "Was passiert in dieser Szene? Beschreibe Bewegungen, Personen und "
                "Veraenderungen zwischen den Frames auf Deutsch in 3-5 Saetzen."
            )

        # Kollage erzeugen — bei Fehler (kein PIL) erstes Frame direkt analysieren
        try:
            collage_b64 = self._build_grid_image(frames_b64, grid_cols=grid_cols)
        except Exception as e:
            logger.warning(f"Video-Kollage fehlgeschlagen ({e}), analysiere erstes Frame.")
            return self.analyze(frames_b64[0], question)

        logger.info(f"Video-Analyse: {len(frames_b64)} Frames als {grid_cols}-Spalten-Grid")
        return self.analyze(collage_b64, question)

    @staticmethod
    def _build_grid_image(
        frames_b64: List[str],
        grid_cols:  int = 3,
        thumb_size: tuple = (320, 240),
    ) -> str:
        """
        Stitcht mehrere Base64-JPEG-Frames zu einem Grid-Bild zusammen.
        Jeder Frame wird auf thumb_size skaliert; das Grid hat grid_cols Spalten.
        Gibt das Ergebnis als Base64-JPEG-String zurueck.
        """
        from PIL import Image  # Import hier — vermeidet harten Fehler wenn PIL fehlt

        images: List[Image.Image] = []
        for f in frames_b64:
            try:
                raw = base64.b64decode(f)
                img = Image.open(io.BytesIO(raw)).convert("RGB")
                img.thumbnail(thumb_size, Image.LANCZOS)
                images.append(img)
            except Exception:
                continue

        if not images:
            raise ValueError("Keine gueltigen Frames fuer Grid-Bild.")

        cols = min(grid_cols, len(images))
        rows = (len(images) + cols - 1) // cols
        tw, th = thumb_size

        # Dunkler Hintergrund (passt zu Jarvis-Theme)
        grid = Image.new("RGB", (cols * tw, rows * th), (20, 20, 30))
        for i, img in enumerate(images):
            r, c = divmod(i, cols)
            # Zentriert in seiner Zelle einfuegen
            x = c * tw + (tw - img.width) // 2
            y = r * th + (th - img.height) // 2
            grid.paste(img, (x, y))

        buf = io.BytesIO()
        grid.save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def update_config(
        self,
        groq_api_key:  Optional[str] = None,
        gemini_api_key: Optional[str] = None,
        ollama_url:    Optional[str] = None,
        ollama_model:  Optional[str] = None,
    ):
        if groq_api_key  is not None: self._groq_key     = groq_api_key
        if gemini_api_key is not None: self._gemini_key   = gemini_api_key
        if ollama_url    is not None: self._ollama_url   = ollama_url.rstrip("/")
        if ollama_model  is not None: self._ollama_model = ollama_model

    # ── Provider-Implementierungen ─────────────────────────────────────────────

    def _analyze_groq(self, image_b64: str, question: str) -> str:
        from openai import OpenAI
        client = OpenAI(
            api_key=self._groq_key,
            base_url="https://api.groq.com/openai/v1",
        )
        last_err: Exception = RuntimeError("Kein Groq Vision-Modell verfuegbar.")  # BUG-016
        for model in self._GROQ_VISION_MODELS:
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"},
                            },
                            {"type": "text", "text": question},
                        ],
                    }],
                    max_tokens=1024,
                    timeout=30,
                )
                result = response.choices[0].message.content or ""
                if result.strip():
                    logger.info(f"Groq Vision: Modell '{model}' verwendet.")
                    return result
            except Exception as e:
                err_str = str(e)
                if "decommissioned" in err_str or "does not exist" in err_str or "404" in err_str:
                    logger.debug(f"Groq Modell '{model}' nicht verfuegbar, naechstes versuchen...")
                    last_err = e
                    continue
                raise  # Anderer Fehler (Netz, Auth) → direkt weitergeben
        raise last_err

    def _analyze_gemini(self, image_b64: str, question: str) -> str:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"gemini-1.5-flash:generateContent?key={self._gemini_key}"
        )
        payload = {
            "contents": [{
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                    {"text": question},
                ]
            }]
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())

        for candidate in body.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    return part["text"]
        return ""

    def _analyze_ollama(self, image_b64: str, question: str) -> str:
        url = f"{self._ollama_url}/api/generate"
        payload = {
            "model":  self._ollama_model,
            "prompt": question,
            "images": [image_b64],
            "stream": False,
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
        return body.get("response", "")

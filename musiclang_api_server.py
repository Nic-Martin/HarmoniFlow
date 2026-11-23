# filepath: musiclang_api_server.py
import base64
import os
import tempfile
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import mido

from ensemble_arranger import arrange_notes_for_ensemble, resolve_ensemble_style

app = FastAPI(title="HarmoniFlow MusicLang API")

_predictor = None
_DEFAULT_SEED_MELODY = [60, 62, 64, 65, 67, 67, 65, 64, 62, 60, 62, 64, 60]


def _get_predictor():
    global _predictor
    if _predictor is not None:
        return _predictor
    try:
        from musiclang_predict import MusicLangPredictor
        _predictor = MusicLangPredictor("musiclang/musiclang-v2")
    except Exception as err:
        print(f"[MUSICLANG API] MusicLangPredictor unavailable, using symbolic arranger: {err}")
        _predictor = False
    return _predictor


class ArrangeRequest(BaseModel):
    style_name: str = "Marching Band"
    notes: Optional[List[int]] = None
    prompt: Optional[str] = None
    seed: int = 42


class ArrangeResponse(BaseModel):
    style_name: str
    midi_base64: str
    engine: str


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/arrange", response_model=ArrangeResponse)
def arrange_music(req: ArrangeRequest):
    resolved_style, _ = resolve_ensemble_style(req.style_name)
    notes = [int(n) for n in (req.notes or []) if 36 <= int(n) <= 96]
    engine_used = "ensemble_arranger"

    predictor = _get_predictor()
    if predictor and not notes:
        try:
            score = predictor.predict(
                nb_bars=8,
                temperature=0.9,
                top_p=0.95,
                seed=req.seed,
            )
            with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
                tmp_midi = tmp.name
            try:
                score.to_midi(tmp_midi)
                mid = mido.MidiFile(tmp_midi)
                extracted = []
                for trk in mid.tracks:
                    for msg in trk:
                        if msg.type == "note_on" and msg.velocity > 0 and msg.channel != 9:
                            if 36 <= msg.note <= 96:
                                extracted.append(int(msg.note))
                if extracted:
                    notes = extracted[:128]
                    engine_used = "musiclang_predict + ensemble_arranger"
            finally:
                if os.path.exists(tmp_midi):
                    os.remove(tmp_midi)
        except Exception as e:
            print(f"[MUSICLANG API] Prediction fallback: {e}")

    if not notes:
        notes = list(_DEFAULT_SEED_MELODY)
        engine_used = "ensemble_arranger_default"

    try:
        arranged = arrange_notes_for_ensemble(notes, style_name=resolved_style)
        if isinstance(arranged, tuple) and len(arranged) >= 2:
            resolved_style, out_mid = arranged[0], arranged[1]
        else:
            out_mid = arranged
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ensemble arrangement failed: {e}")

    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as out_tmp:
        out_path = out_tmp.name
    try:
        out_mid.save(out_path)
        with open(out_path, "rb") as f:
            encoded_midi = base64.b64encode(f.read()).decode("ascii")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to serialize MIDI: {e}")
    finally:
        if os.path.exists(out_path):
            os.remove(out_path)

    return ArrangeResponse(
        style_name=resolved_style,
        midi_base64=encoded_midi,
        engine=engine_used,
    )
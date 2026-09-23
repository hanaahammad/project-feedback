"""Audio/video transcription extension point.

This module exists to give the rest of the app (specifically the
`POST /projects/{project_id}/cycles/{cycle_id}/uploads/{upload_id}/transcribe`
endpoint in `app/uploads.py`) a single, mockable seam for calling out to a
transcription provider for a `pending` `audio`/`video` `MeetingUpload`.

Per #19's Out of scope: which provider to call, model/prompt choice, and
API key/credential management are deliberately not addressed here, mirroring
#12's `app/ai_clustering.py`/`generate_cluster_suggestions` pattern exactly.
No transcription SDK is added to `pyproject.toml` -- `transcribe_audio` is
the seam a future task wires a real provider in behind. Until then it raises
`NotImplementedError`, which the caller (the transcribe endpoint) catches
and turns into `status: "failed"`, so the absence of a configured provider
behaves the same as a failed/timed-out call, never as a request error, and
requires no real network call or credential in tests or in CI.
"""


def transcribe_audio(file_path: str) -> str:
    """Ask a transcription provider to transcribe the audio/video file at
    `file_path`, returning the resulting transcript text.

    No provider is wired up in this task (see the module docstring), so
    this raises `NotImplementedError` unconditionally. Callers must catch
    exceptions from this function (the transcribe endpoint does) and tests
    must monkeypatch/stub it rather than relying on -- or requiring -- a
    real implementation.
    """
    raise NotImplementedError(
        "No transcription provider is configured. Provider selection, "
        "model/prompt choice, and credential management are out of scope "
        "for this task -- transcribe_audio is the extension point a "
        "future task implements behind."
    )

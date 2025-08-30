from fastapi import FastAPI, UploadFile, File, Query, Header
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse
import tempfile, subprocess, os, shutil, uuid

APP_TOKEN = os.getenv("OCR_TOKEN", "changeme")
app = FastAPI(title="Tiny OCR API (Fast MVP OCR)")

@app.middleware("http")
async def force_close_conn(request, call_next):
    """
    Ensures that the 'Connection: close' header is present in responses.
    This helps prevent client-side timeouts (like n8n's) for long-running
    synchronous requests by signaling that the connection should be closed
    after the response is fully sent.
    """
    resp = await call_next(request)
    # Check if 'connection' header is already set (case-insensitive)
    if "connection" not in {k.lower() for k in resp.headers.keys()}:
        resp.headers["Connection"] = "close"
    return resp

@app.get("/")
def root():
    return PlainTextResponse("ok")

def run(cmd: str):
    p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stdout)
    return p.stdout

@app.post("/ocr")
async def ocr_pdf(
    file: UploadFile = File(...),
    lang: str = Query("eng", description="tesseract language(s), e.g. eng or eng+spa"),
    pages: str | None = Query(None, description="e.g. 1-2 or 1,3,5"),
    tesseract_oem: int | None = Query(1, ge=0, le=3, description="Tesseract OCR Engine Mode (0-3). 1=LSTM-only is usually fast + accurate."),
    tesseract_psm: int | None = Query(6, ge=0, le=13, description="Tesseract Page Segmentation Mode (0-13). 6 often speeds up uniform pages."),
    x_app_token: str | None = Header(None, alias="x-app-token")
):
    if x_app_token != APP_TOKEN:
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)

    work = tempfile.mkdtemp(prefix="ocr_")
    try:
        in_pdf  = os.path.join(work, "in.pdf")
        out_pdf = os.path.join(work, "out.pdf")
        sidecar = os.path.join(work, "text.txt")

        with open(in_pdf, "wb") as f:
            f.write(await file.read())

        cmd = [
            "ocrmypdf",
            "--optimize", "0",              # no extra compression work (fastest)
            "--language", lang,
            "--jobs", "1",                  # keep serial for predictable resource usage
            "--tesseract-timeout", "120",   # guardrail for pathological inputs
            "--sidecar", sidecar
        ]

        # Pass through safe Tesseract speed knobs when explicitly provided
        if tesseract_oem is not None:
            cmd += ["--tesseract-oem", str(tesseract_oem)]
        if tesseract_psm is not None:
            cmd += ["--tesseract-pagesegmode", str(tesseract_psm)]
        if pages:
            cmd += ["--pages", pages]
        cmd += [in_pdf, out_pdf]

        # Execute ocrmypdf
        run(" ".join(cmd))

        text = ""
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8", errors="ignore") as s:
                text = s.read()
                
        return JSONResponse({
            "ok": True,
            "pages": pages or "all",
            "lang": lang,
            "text": text
        })
    except Exception as e:
        # Catch all exceptions during processing and return a 500
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        # Ensure temporary working directory is cleaned up
        shutil.rmtree(work, ignore_errors=True)


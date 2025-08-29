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
    make_searchable: bool = Query(True),
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
            "--rotate-pages",
            "--optimize", "0",          # lighter processing
            "--language", lang,
            "--jobs", "1",              # keep resource light
            "--tesseract-timeout", "120", # Timeout for tesseract part of ocrmypdf
            "--force-ocr",              # always OCR (fast + simple)
            "--sidecar", sidecar
        ]
        if pages:
            cmd += ["--pages", pages]
        cmd += [in_pdf, out_pdf]

        # Execute ocrmypdf
        run(" ".join(cmd))

        text = ""
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8", errors="ignore") as s:
                text = s.read()

        token = str(uuid.uuid4())
        
        cached_pdf = os.path.join("/tmp", f"{token}.pdf")
        shutil.copyfile(out_pdf, cached_pdf)

        return JSONResponse({
            "ok": True,
            "pages": pages or "all",
            "lang": lang,
            "text": text,
            "searchable_pdf": f"/download/{token}"
        })
    except Exception as e:
        # Catch all exceptions during processing and return a 500
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    finally:
        # Ensure temporary working directory is cleaned up
        shutil.rmtree(work, ignore_errors=True)

@app.get("/download/{token}")
def download(token: str):
    path = os.path.join("/tmp", f"{token}.pdf")
    if not os.path.exists(path):
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return FileResponse(path, media_type="application/pdf", filename="searchable.pdf")
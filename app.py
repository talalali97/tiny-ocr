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

# --- Fast preflight helpers to avoid unnecessary OCR ---
def get_page_count(pdf_path: str) -> int:
    """Return number of pages using qpdf or pdfinfo."""
    try:
        out = run(f"qpdf --show-npages '{pdf_path}'")
        n = int(out.strip())
        if n > 0:
            return n
    except Exception:
        pass
    try:
        info = run(f"pdfinfo '{pdf_path}' | grep -i '^Pages:' | awk '{{print $2}}'")
        n = int(info.strip())
        if n > 0:
            return n
    except Exception:
        pass
    return 0

def page_has_text(pdf_path: str, page: int, min_chars: int) -> bool:
    """Return True if the given page seems to contain text >= min_chars."""
    try:
        txt = run(f"pdftotext -layout -nopgbrk -f {page} -l {page} '{pdf_path}' -")
        return len(txt.strip()) >= min_chars
    except Exception:
        return False

@app.post("/ocr")
async def ocr_pdf(
    file: UploadFile = File(...),
    lang: str = Query("eng", description="tesseract language(s), e.g. eng or eng+spa"),
    pages: str | None = Query(None, description="e.g. 1-2 or 1,3,5"),
    make_searchable: bool = Query(False),
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

        # Prefer extracting text from the final PDF so we capture
        # both existing text layers and any new OCR text. Fall back
        # to the sidecar if pdftotext is unavailable or returns empty.
        text = ""
        try:
            pdf_text = run(f"pdftotext -layout -nopgbrk '{out_pdf}' -")
            if pdf_text and pdf_text.strip():
                text = pdf_text
        except Exception:
            pass
        if not text and os.path.exists(sidecar):
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

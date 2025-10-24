from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse, StreamingResponse
import hashlib
from PyPDF2 import PdfReader, PdfWriter
import fitz
from PIL import Image
import imagehash
import io
import qrcode
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

app = FastAPI(title="PDF Compare & QR API")

def sha256_file_bytes(file_bytes, block_size=65536):
    h = hashlib.sha256()
    for i in range(0, len(file_bytes), block_size):
        h.update(file_bytes[i:i+block_size])
    return h.hexdigest()

def text_hash_bytes(file_bytes):
    try:
        reader = PdfReader(io.BytesIO(file_bytes))
        text_acc = []
        for page in reader.pages:
            text_acc.append(page.extract_text() or "")
        text = "\n".join(text_acc).strip()
        return hashlib.sha256(text.encode("utf-8")).hexdigest(), text
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF text extraction failed: {str(e)}")

def pdf_page_hashes_bytes(file_bytes, zoom=1.0, hash_size=16):
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        hashes = []
        for p in range(len(doc)):
            page = doc.load_page(p)
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            mode = "RGB" if pix.n < 4 else "RGBA"
            img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            h = imagehash.phash(img, hash_size=hash_size)
            hashes.append(h)
        return hashes
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"PDF page hashing failed: {str(e)}")

def compare_pdf_visual_bytes(bytes_a, bytes_b, max_hamming_per_page=6, match_ratio_threshold=0.8):
    ha = pdf_page_hashes_bytes(bytes_a)
    hb = pdf_page_hashes_bytes(bytes_b)
    n = min(len(ha), len(hb))
    matches = 0
    for i in range(n):
        d = ha[i] - hb[i]
        if d <= max_hamming_per_page:
            matches += 1
    ratio = matches / n if n > 0 else 0
    return {
        "pages_a": len(ha),
        "pages_b": len(hb),
        "compared_pages": n,
        "matches": matches,
        "match_ratio": ratio,
        "same_visual": ratio >= match_ratio_threshold
    }

@app.post("/compare")
async def compare_pdfs_api(file_a: UploadFile = File(...), file_b: UploadFile = File(...)):
    if file_a.content_type != "application/pdf" or file_b.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Both files must be PDFs")

    bytes_a = await file_a.read()
    bytes_b = await file_b.read()

    sha_a = sha256_file_bytes(bytes_a)
    sha_b = sha256_file_bytes(bytes_b)

    text_hash_a, text_a = text_hash_bytes(bytes_a)
    text_hash_b, text_b = text_hash_bytes(bytes_b)

    visual_res = compare_pdf_visual_bytes(bytes_a, bytes_b)

    result = {
        "sha256": {"file_a": sha_a, "file_b": sha_b, "identical": sha_a == sha_b},
        "text_hash": {"file_a": text_hash_a, "file_b": text_hash_b, "identical": text_hash_a == text_hash_b},
        "visual": visual_res,
        "text_a": text_a,
        "text_b": text_b,
    }
    return JSONResponse(content=result)

@app.post("/add-qr")
async def add_qr_to_pdf(
    file: UploadFile = File(...),
    u_key: str = Form(...),
    id: str = Form(...)
):
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF")

    pdf_bytes = await file.read()

    qr_data = f"http://192.168.101.42:3007/verified-document/{id}"
    qr_img = qrcode.make(qr_data)

    qr_buffer = io.BytesIO()
    qr_img.save(qr_buffer, format="PNG")
    qr_buffer.seek(0)

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[-1]
    rect = page.rect

    qr_size = 80
    x = rect.width - qr_size - 20
    y = rect.height - qr_size - 20

    page.insert_image(
        fitz.Rect(x, y, x + qr_size, y + qr_size),
        stream=qr_buffer.getvalue(),
        keep_proportion=True
    )

    output_pdf = io.BytesIO()
    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()
    output_pdf.seek(0)

    return StreamingResponse(
        output_pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": "attachment; filename=pdf_with_qr.pdf"}
    )
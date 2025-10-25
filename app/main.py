from fastapi import FastAPI, File, UploadFile, HTTPException, Form
from fastapi.responses import JSONResponse, StreamingResponse
import hashlib
from PyPDF2 import PdfReader
import fitz
from PIL import Image
import imagehash
import io
import qrcode
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="PDF Compare & QR API PT Rejoso Manis Indo")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    matches = sum(1 for i in range(n) if ha[i] - hb[i] <= max_hamming_per_page)
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

    return JSONResponse(content={
        "sha256": {"file_a": sha_a, "file_b": sha_b, "identical": sha_a == sha_b},
        "text_hash": {"file_a": text_hash_a, "file_b": text_hash_b, "identical": text_hash_a == text_hash_b},
        "visual": visual_res,
        "text_a": text_a,
        "text_b": text_b,
    })

@app.post("/add-qr")
async def add_qr_to_pdf(
    file: UploadFile = File(...),
    u_key: str = Form(...),
    id: str = Form(...),
    x: float = Form(...),
    y: float = Form(...),
    page: int = Form(...),
    pageWidth: float = Form(...),
    pageHeight: float = Form(...),
):

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File harus PDF")

    pdf_bytes = await file.read()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    if page < 1 or page > len(doc):
        raise HTTPException(status_code=400, detail="Nomor halaman tidak valid")

    target_page = doc.load_page(page - 1)

    pdf_width = target_page.rect.width
    pdf_height = target_page.rect.height

    x_ratio = x / pageWidth
    y_ratio = y / pageHeight

    pdf_x = x_ratio * pdf_width

    pdf_y = y_ratio * pdf_height

    qr_data = f"http://192.168.101.42:3007/verified-document/{id}"
    qr_img = qrcode.make(qr_data)
    qr_buffer = io.BytesIO()
    qr_img.save(qr_buffer, format="PNG")

    qr_size_pdf = 60.0

    rect = fitz.Rect(pdf_x, pdf_y, pdf_x + qr_size_pdf, pdf_y + qr_size_pdf)

    target_page.insert_image(rect, stream=qr_buffer.getvalue())

    output = io.BytesIO()
    doc.save(output)
    doc.close()
    output.seek(0)

    return StreamingResponse(
        output,
        media_type="application/pdf",
        headers={"Content-Disposition": "inline; filename=pdf_with_qr.pdf"},
    )
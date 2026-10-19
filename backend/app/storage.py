"""Storage abstraction: local in development, replaceable with S3/Azure implementation."""
import io
import os
import zipfile
from pathlib import Path
from uuid import uuid4

ROOT=Path(os.getenv("UPLOAD_DIR","./uploads"))
ALLOWED={"application/pdf":".pdf","image/png":".png","image/jpeg":".jpg","application/vnd.openxmlformats-officedocument.wordprocessingml.document":".docx","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":".xlsx"}
MAX_BYTES=999_999_999
MAX_BASE64_CHARS=((MAX_BYTES+2)//3)*4
def valid_content(mime_type:str,data:bytes):
    if mime_type=="application/pdf": return data.startswith(b"%PDF-")
    if mime_type=="image/png": return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type=="image/jpeg": return data.startswith(b"\xff\xd8\xff")
    if mime_type in ("application/vnd.openxmlformats-officedocument.wordprocessingml.document","application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as package:
                names=set(package.namelist()); marker="word/document.xml" if "wordprocessingml" in mime_type else "xl/workbook.xml"
                return "[Content_Types].xml" in names and marker in names
        except zipfile.BadZipFile: return False
    return False
def save(filename:str,mime_type:str,data:bytes):
    if mime_type not in ALLOWED or not data or len(data)>MAX_BYTES: raise ValueError("Files must be valid PDF, PNG, JPEG, DOCX, or XLSX files smaller than 1 GB.")
    if Path(filename).suffix.lower() not in (ALLOWED[mime_type],".jpeg" if mime_type=="image/jpeg" else ALLOWED[mime_type]): raise ValueError("File extension does not match the declared file type.")
    if not valid_content(mime_type,data): raise ValueError("File content does not match the selected file type.")
    ROOT.mkdir(parents=True,exist_ok=True); key=uuid4().hex+ALLOWED[mime_type]; (ROOT/key).write_bytes(data); return key

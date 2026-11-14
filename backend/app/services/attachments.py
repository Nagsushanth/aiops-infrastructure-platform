
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status


PROJECT_ROOT = Path(__file__).resolve().parents[3]

UPLOAD_DIRECTORY = Path(
    os.getenv(
        "AIOPS_UPLOAD_DIR",
        str(PROJECT_ROOT / "private_uploads"),
    )
).expanduser().resolve()

MAX_FILE_SIZE = 10 * 1024 * 1024

ALLOWED_FILES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
}


def validate_file_content(
    extension: str,
    content: bytes,
    file_path: Path | None = None,
) -> None:
    """Validate file signatures and basic content."""

    if extension == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("Invalid PDF file signature.")

    elif extension == ".png":
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Invalid PNG file signature.")

    elif extension in {".jpg", ".jpeg"}:
        if not content.startswith(b"\xff\xd8\xff"):
            raise ValueError("Invalid JPEG file signature.")

    elif extension == ".docx":
        if not content.startswith(b"PK"):
            raise ValueError("Invalid DOCX file signature.")

        if file_path is None or not zipfile.is_zipfile(file_path):
            raise ValueError("Invalid DOCX archive.")

        try:
            with zipfile.ZipFile(file_path) as archive:
                names = archive.namelist()

                if "[Content_Types].xml" not in names:
                    raise ValueError("Invalid DOCX structure.")

                if "word/document.xml" not in names:
                    raise ValueError("Invalid DOCX document.")

        except zipfile.BadZipFile as exc:
            raise ValueError("Invalid DOCX archive.") from exc

    elif extension in {".txt", ".csv", ".json"}:
        if b"\x00" in content:
            raise ValueError("Binary content is not allowed.")

        try:
            decoded = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(
                "File must contain valid UTF-8 text."
            ) from exc

        if extension == ".json":
            try:
                json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise ValueError("Invalid JSON document.") from exc


async def store_attachment(upload: UploadFile) -> dict:
    """Validate and persist an uploaded file privately."""

    original_filename = Path(upload.filename or "").name.strip()

    if (
        not original_filename
        or len(original_filename) > 255
        or original_filename in {".", ".."}
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filename.",
        )

    extension = Path(original_filename).suffix.lower()

    if extension not in ALLOWED_FILES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="This file type is not supported.",
        )

    storage_filename = f"{uuid4().hex}{extension}"

    try:
        UPLOAD_DIRECTORY.mkdir(
            parents=True,
            exist_ok=True,
            mode=0o700,
        )
        os.chmod(UPLOAD_DIRECTORY, 0o700)

        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".upload-",
            dir=UPLOAD_DIRECTORY,
        )

    except OSError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Private file storage is unavailable.",
        ) from exc

    temporary_path = Path(temporary_name)
    destination = UPLOAD_DIRECTORY / storage_filename

    total_size = 0
    digest = hashlib.sha256()
    first_chunk = b""

    try:
        with os.fdopen(file_descriptor, "wb") as output:
            while True:
                chunk = await upload.read(1024 * 1024)

                if not chunk:
                    break

                total_size += len(chunk)

                if total_size > MAX_FILE_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Maximum file size is 10 MB.",
                    )

                if not first_chunk:
                    first_chunk = chunk[:16]

                digest.update(chunk)
                output.write(chunk)

        if total_size == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Empty files are not allowed.",
            )

        try:
            validation_content = (
                temporary_path.read_bytes()
                if extension in {".txt", ".csv", ".json"}
                else first_chunk
            )

            validate_file_content(
                extension,
                validation_content,
                temporary_path,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination)

        return {
            "original_filename": original_filename,
            "storage_filename": storage_filename,
            "content_type": ALLOWED_FILES[extension],
            "size_bytes": total_size,
            "sha256": digest.hexdigest(),
            "storage_path": destination,
        }

    except Exception:
        temporary_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise

    finally:
        await upload.close()


def delete_stored_attachment(storage_filename: str) -> None:
    """Delete a stored file using its server-generated filename."""

    if (
        not storage_filename
        or Path(storage_filename).name != storage_filename
    ):
        raise ValueError("Invalid storage filename.")

    candidate = (UPLOAD_DIRECTORY / storage_filename).resolve()

    if candidate.parent != UPLOAD_DIRECTORY:
        raise ValueError("Invalid attachment storage path.")

    candidate.unlink(missing_ok=True)

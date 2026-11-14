
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    message_id: int
    original_filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime


class AttachmentUploadResponse(BaseModel):
    attachment: AttachmentResponse
    message_id: int
    message_content: str

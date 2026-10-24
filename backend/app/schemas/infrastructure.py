from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InfrastructureRequirements(BaseModel):
    """Collected requirements for an infrastructure request."""

    model_config = ConfigDict(extra="forbid")

    cloud_provider: Literal["aws", "azure", "gcp"] | None = None
    resource_type: str | None = Field(default=None, max_length=100)
    environment: Literal[
        "development", "testing", "staging", "production"
    ] | None = None

    region: str | None = Field(default=None, max_length=100)
    vcpus: int | None = Field(default=None, ge=1, le=256)
    memory_gb: int | None = Field(default=None, ge=1, le=4096)
    public_access: bool | None = None
    operating_system: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def validate_resource_requirements(self):
        if self.resource_type and not self.resource_type.strip():
            raise ValueError("Resource type cannot be empty.")

        if self.region and not self.region.strip():
            raise ValueError("Region cannot be empty.")

        return self


class ValidatedInfrastructureSpecification(
    InfrastructureRequirements
):
    """Infrastructure specification ready for the next review stage."""

    cloud_provider: Literal["aws", "azure", "gcp"]
    resource_type: str = Field(min_length=1, max_length=100)
    environment: Literal[
        "development", "testing", "staging", "production"
    ]

    region: str = Field(min_length=1, max_length=100)
    vcpus: int = Field(ge=1, le=256)
    memory_gb: int = Field(ge=1, le=4096)
    public_access: bool
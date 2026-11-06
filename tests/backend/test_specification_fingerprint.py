
import hashlib
import json

from backend.app.schemas.infrastructure import (
    ValidatedInfrastructureSpecification,
)
from backend.app.services.terraform.execution import (
    _canonical_specification,
)
from backend.app.workers.tasks import _fingerprint_specification
from backend.app.api.infrastructure_operations import (
    fingerprint_specification,
)
from backend.app.api.approvals import _fingerprint_specification as approval_fingerprint
from backend.app.api.terraform_plans import (
    _fingerprint_specification as plan_api_fingerprint,
)


def test_all_services_generate_identical_unicode_specification_fingerprint():
    specification = ValidatedInfrastructureSpecification(
        cloud_provider="aws",
        resource_type="ec2",
        environment="development",
        region="ap-south-1",
        vcpus=2,
        memory_gb=4,
        public_access=False,
        operating_system="Amazon Linux – édition développeur",
    )

    canonical = json.dumps(
        specification.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")

    expected = hashlib.sha256(canonical).hexdigest()

    fingerprints = {
        hashlib.sha256(
            _canonical_specification(specification)
        ).hexdigest(),
        _fingerprint_specification(specification),
        fingerprint_specification(specification),
        approval_fingerprint(specification),
        plan_api_fingerprint(specification),
    }

    assert fingerprints == {expected}

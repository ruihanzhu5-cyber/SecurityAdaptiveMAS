from dataclasses import dataclass, field, asdict


def string_list(value, name):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise ValueError(f"{name}_requires_string_list")
    if len(set(value)) != len(value):
        raise ValueError(f"{name}_duplicates")
    return tuple(value)


@dataclass(frozen=True)
class ArtifactRef:
    object_id: str
    version: int = 1
    view_id: str = "full"

    @property
    def key(self):
        return f"{self.object_id}@{self.version}:{self.view_id}"


@dataclass
class WorkItem:
    work_id: str
    requester_id: str
    goal_revision: int
    instruction_evidence_id: str
    input_refs: tuple
    requested_tools: tuple
    output_fields: tuple
    producer_id: str = ""
    status: str = "requested"
    repair_attempts: int = 0
    result_refs: list = field(default_factory=list)


@dataclass(frozen=True)
class ArtifactRecord:
    ref: ArtifactRef
    producer_id: str
    producer_work_id: str
    source_call_id: str | None
    source_tool: str | None
    payload_kind: str
    parent_refs: tuple
    created_sequence: int
    available_views: tuple
    fields: tuple = ("result",)
    data_dependencies: tuple = ()
    record_lineage: tuple = ()


@dataclass(frozen=True)
class RuntimeStamp:
    graph_version: int
    observation_sequence: int
    goal_revision: int
    object_epoch: int


@dataclass(frozen=True)
class DeliveryRequest:
    request_id: str
    requester_id: str
    for_work_id: str
    artifact_ref: ArtifactRef
    required_fields: tuple
    current_graph_version: int


@dataclass
class ReconfigurationCandidate:
    candidate_id: str
    action: str
    base_stamp: RuntimeStamp
    graph_operations: tuple = ()
    object_dispositions: dict = field(default_factory=dict)
    pending_work: str | None = None
    delivery_refs: tuple = ()
    evidence_ids: tuple = ()
    cost_proxy: dict = field(default_factory=dict)
    risk_projection: dict = field(default_factory=dict)
    status: str = "proposed"
    rejection_reason: str | None = None
    validation: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class EvidenceField:
    name: str
    value: str | int | float
    source_ref: ArtifactRef
    source_path: str
    source_span: tuple[int,int] | None
    extraction: str
    status: str
    source_call_id: str | None = None
    normalization_basis: str | None = None


@dataclass(frozen=True)
class EvidenceView:
    parent_ref: ArtifactRef
    fields: tuple[EvidenceField,...]
    covered_required_fields: tuple[str,...]
    missing_required_fields: tuple[str,...]
    contains_raw_untrusted_body: bool
    rendered_content: str

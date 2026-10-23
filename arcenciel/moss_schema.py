from typing import Literal, NotRequired, TypeAlias, TypedDict

BoxLabel: TypeAlias = Literal["character", "object", "text"]


class MossModel(TypedDict):
    name: str
    modelCapabilities: list[str]
    storageState: str


class MossDefaults(TypedDict):
    steps: int
    cfg: float
    shift: float
    samplerName: str
    scheduler: str
    width: int
    height: int


class MossProfile(TypedDict):
    available: bool
    modes: list[str]
    maxBoxes: int
    coordinateSpace: Literal["normalized_1000"]
    defaults: MossDefaults


class MossBox(TypedDict):
    label: BoxLabel
    caption: str
    bbox: list[int]
    text: NotRequired[str]


class MossLayout(TypedDict):
    version: Literal[1]
    coordinateSpace: Literal["normalized_1000"]
    canvas: Literal["single"]
    items: list[MossBox]
    meta: NotRequired[list[str]]


class MossOptions(TypedDict, total=False):
    shift: float
    layout: MossLayout


class MossLora(TypedDict):
    name: str
    weight: float


class MossPayload(TypedDict):
    mode: Literal["txt2img"]
    prompt: str
    negativePrompt: str
    modelName: str
    seed: int
    batchSize: Literal[1]
    loras: list[MossLora]
    sfwMode: bool
    steps: int
    cfg: float
    samplerName: str
    scheduler: str
    width: int
    height: int
    moss: MossOptions

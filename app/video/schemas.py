"""Input and immutable VideoSpec contracts shared by the API and render worker."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StrictBool, model_validator

from app.creative.schemas import SequentialCaptions, SingleCaption
from app.schemas import Schema

TEMPLATE_VERSION = 1
SPEC_VERSION = 1
RENDERER_VERSION = "1.0.0"
Color = Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
ShortText = Annotated[str, Field(min_length=1, max_length=180)]
CaptionCopy = SingleCaption | SequentialCaptions


class CaptionStyle(Schema):
    position: Literal["TOP", "CENTER", "BOTTOM"] = "BOTTOM"
    font_size: int = Field(66, ge=44, le=76)
    color: Color = "#FFFFFF"
    backdrop: Color = "#101010"
    safe_margin: int = Field(96, ge=72, le=140)


class DisplayOptions(Schema):
    style: CaptionStyle = Field(default_factory=CaptionStyle)
    fit: Literal["cover", "contain"] = "cover"
    brand_label: ShortText | None = None
    cta_overlay: ShortText | None = None
    audio: Literal["SILENT", "BACKGROUND"] = "SILENT"


class CreateVideoSpec(DisplayOptions):
    expected_concept_revision: int = Field(ge=1)
    template: Literal["TEXT_OVER_BROLL", "PRODUCT_REVEAL"] = "TEXT_OVER_BROLL"
    background_asset_id: UUID | None = None
    product_asset_ids: list[UUID] = Field(default_factory=list, max_length=5)
    final_captions: CaptionCopy | None = None
    confirm_legacy_captions: StrictBool = False
    confirm_media_use: StrictBool

    @model_validator(mode="after")
    def template_assets(self):
        if self.template == "TEXT_OVER_BROLL" and (
            self.background_asset_id is None or self.product_asset_ids
        ):
            raise ValueError("Text over B-roll requires one video and no product images")
        if self.template == "PRODUCT_REVEAL" and (
            self.background_asset_id is not None
            or not self.product_asset_ids
            or len(set(self.product_asset_ids)) != len(self.product_asset_ids)
            or self.audio != "SILENT"
        ):
            raise ValueError("Product reveal requires distinct authorized images and silent audio")
        return self


class OptionChanges(Schema):
    style: CaptionStyle | None = None
    fit: Literal["cover", "contain"] | None = None
    brand_label: ShortText | None = None
    cta_overlay: ShortText | None = None
    audio: Literal["SILENT", "BACKGROUND"] | None = None


class VideoSpecPatch(Schema):
    expected_revision: int = Field(ge=1)
    changes: OptionChanges
    confirm_media_use: StrictBool = False


class ImportStock(Schema):
    search_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    provider_asset_id: str = Field(pattern=r"^[1-9][0-9]{0,18}$")
    media_type: Literal["IMAGE", "VIDEO"]
    confirm_rights: StrictBool


class RenderRequest(Schema):
    expected_revision: int = Field(ge=1)


class FrameText(Schema):
    text: ShortText
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)


class AssetRef(Schema):
    asset_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    object_key: str
    extension: Literal["mp4", "webm", "jpg", "png", "webp"]


class ProductScene(Schema):
    asset: AssetRef
    start_frame: int = Field(ge=0)
    end_frame: int = Field(gt=0)


class VideoPlan(DisplayOptions):
    schema_version: Literal[1] = SPEC_VERSION
    template_version: Literal[1] = TEMPLATE_VERSION
    renderer_version: Literal["1.0.0"] = RENDERER_VERSION
    template: Literal["TEXT_OVER_BROLL", "PRODUCT_REVEAL"]
    brand_id: UUID
    campaign_id: UUID
    campaign_revision: int = Field(ge=1)
    concept_id: UUID
    concept_revision: int = Field(ge=1)
    background: AssetRef | None = None
    products: list[ProductScene] = Field(default_factory=list, max_length=5)
    width: Literal[1080] = 1080
    height: Literal[1920] = 1920
    fps: Literal[30] = 30
    duration_frames: int = Field(ge=150, le=1800, multiple_of=30)
    format: Literal["MP4"] = "MP4"
    codec: Literal["H264"] = "H264"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    caption_treatment: Literal["SINGLE_CAPTION", "SEQUENTIAL_CAPTIONS"]
    captions: list[FrameText] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def check_timeline(self):
        if self.template == "TEXT_OVER_BROLL" and (not self.background or self.products):
            raise ValueError("Text over B-roll requires one background")
        if self.template == "PRODUCT_REVEAL":
            if self.background or not self.products or self.audio != "SILENT":
                raise ValueError("Product reveal requires silent product images")
            cursor = 0
            for scene in self.products:
                if scene.start_frame != cursor or scene.end_frame - cursor < 40:
                    raise ValueError("Product scenes need ordered ranges of at least 40 frames")
                cursor = scene.end_frame
            if cursor != self.duration_frames:
                raise ValueError("Product scenes must cover the video")
        expected = {1, 2} if self.caption_treatment == "SINGLE_CAPTION" else {4}
        if len(self.captions) not in expected:
            raise ValueError("Caption count does not match the treatment")
        cursor = 0
        for caption in self.captions:
            if caption.start_frame != cursor or caption.end_frame <= cursor:
                raise ValueError("Caption frames must be continuous and ordered")
            cursor = caption.end_frame
        if cursor != self.duration_frames:
            raise ValueError("Caption timeline must cover the requested duration")
        return self

"""Versioned planning inputs; unknown facts stay explicit throughout execution."""
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo
import re
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class PlanningValue(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DayWindow(PlanningValue):
    day_index: int = Field(ge=0, le=29)
    start: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Daily window end must follow start")
        return self


class CitySegment(PlanningValue):
    city: str = Field(min_length=1, max_length=32)
    start_day: int = Field(ge=0, le=29)
    end_day: int = Field(ge=0, le=29)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_day < self.start_day:
            raise ValueError("City segment end must follow start")
        return self


class RevisionScope(PlanningValue):
    day_indices: list[int] = Field(default_factory=list, max_length=30)
    activity_ids: list[str] = Field(default_factory=list, max_length=300)
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def valid(self):
        if any(i < 0 or i > 29 for i in self.day_indices):
            raise ValueError("Invalid scope day")
        if (self.start_at is None) != (self.end_at is None):
            raise ValueError("Time scope requires both boundaries")
        if self.start_at and (self.start_at.tzinfo is None or self.end_at.tzinfo is None or self.end_at <= self.start_at):
            raise ValueError("Scope requires ordered timezone-aware times")
        return self


class PlanningInput(PlanningValue):
    _execution_locks: list[dict] = PrivateAttr(default_factory=list)
    _baseline_times: dict[str, str] = PrivateAttr(default_factory=dict)
    timezone: str = "Asia/Shanghai"
    day_start: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    day_end: str = Field(default="20:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    day_windows: list[DayWindow] = Field(default_factory=list, max_length=30)
    city_segments: list[CitySegment] = Field(default_factory=list, max_length=30)
    # Structured records are additive to the existing booking ledger. Their IDs and
    # versions are checked again by Java at confirmation, not trusted from the LLM.
    commitments: list[dict] = Field(default_factory=list, max_length=300)
    hotel_stays: list[dict] = Field(default_factory=list, max_length=30)
    meal_windows: list[dict] = Field(default_factory=list, max_length=8)
    opening_windows: list[dict] = Field(default_factory=list, max_length=500)
    locks: list[dict] = Field(default_factory=list, max_length=300)
    completed_activity_ids: list[str] = Field(default_factory=list, max_length=300)
    arrival_at: datetime | None = None
    departure_at: datetime | None = None
    budget_mode: Literal["estimate", "confirmed"] = "estimate"
    budget_cents: int | None = Field(default=None, ge=0, le=1_000_000_000)
    revision_scope: RevisionScope | None = None

    @model_validator(mode="after")
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError,KeyError):
            raise ValueError("Unknown planning timezone") from None
        if self.day_end <= self.day_start:
            raise ValueError("Daily window end must follow start")
        occupied = set()
        for segment in self.city_segments:
            days = set(range(segment.start_day, segment.end_day + 1))
            if occupied & days:
                raise ValueError("City segments must not overlap")
            occupied.update(days)
        for value in [self.arrival_at, self.departure_at]:
            if value and value.tzinfo is None:
                raise ValueError("Arrival/departure requires timezone")
        if self.arrival_at and self.departure_at and self.departure_at <= self.arrival_at:
            raise ValueError("Departure must follow arrival")
        clock = lambda value: isinstance(value,str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d",value)
        for item in self.meal_windows:
            if item.get("type") not in {"breakfast","lunch","dinner","snack"} or not clock(item.get("start")) or not clock(item.get("end")) or item["start"] >= item["end"] or not isinstance(item.get("duration_minutes",60),int) or not 1 <= item.get("duration_minutes",60) <= 240:
                raise ValueError("Meal window must have ordered times and bounded duration")
        for item in self.opening_windows:
            if not item.get("poi_id") or not isinstance(item.get("windows",[]),list):
                raise ValueError("Opening facts require POI and windows")
            for interval in item.get("windows",[]):
                if not clock(interval.get("start")) or not clock(interval.get("end")) or interval["end"] <= interval["start"]:
                    raise ValueError("Opening windows must be ordered daily times")
            if item.get("last_entry") and not clock(item["last_entry"]):
                raise ValueError("Invalid last entry time")
        for item in self.commitments:
            if item.get("status") in {"cancelled","canceled"}:
                continue
            if not item.get("id") or not isinstance(item.get("day_index"),int) or not 0 <= item["day_index"] <= 29:
                raise ValueError("Commitment requires ID and valid day")
            if item.get("time_mode","fixed") not in {"fixed","entry_window"}:
                raise ValueError("Commitment time mode must be fixed or entry_window")
            if not isinstance(item.get("duration_minutes",60),int) or not 1 <= item.get("duration_minutes",60) <= 1440:
                raise ValueError("Commitment duration must be bounded positive minutes")
            try:
                if not item.get("start_at") and not item.get("end_at"):
                    continue  # Legacy bookings remain explicit unverified inputs.
                start,end = (datetime.fromisoformat(str(item[key]).replace("Z","+00:00")) for key in ("start_at","end_at"))
                if not start.tzinfo or not end.tzinfo or end <= start:
                    raise ValueError()
            except (KeyError,ValueError,TypeError):
                raise ValueError("Commitment requires ordered timezone-aware times") from None
            amount = item.get("amount_cents")
            if amount is not None and (isinstance(amount,bool) or not isinstance(amount,int) or not 0 <= amount <= 1_000_000_000):
                raise ValueError("Confirmed amount must be nonnegative integer cents")
        for item in self.locks:
            if not item.get("activity_id") or item.get("mode","content") not in {"content","time"}:
                raise ValueError("Lock requires activity and content/time mode")
            if item.get("mode") == "time":
                try:
                    start,end = (datetime.fromisoformat(str(item[key]).replace("Z","+00:00")) for key in ("start_at","end_at"))
                    if not start.tzinfo or not end.tzinfo or end <= start:
                        raise ValueError()
                except (KeyError,ValueError,TypeError):
                    raise ValueError("Time lock requires ordered timezone-aware times") from None
        for item in [*self.hotel_stays,*self.commitments]:
            for key in ("location","origin_location","destination_location"):
                if item.get(key) is not None:
                    location=item[key]
                    try:
                        longitude,latitude=float(location["longitude"]),float(location["latitude"])
                        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                            raise ValueError()
                    except (KeyError,ValueError,TypeError):
                        raise ValueError("Planning location requires valid longitude/latitude") from None
        return self

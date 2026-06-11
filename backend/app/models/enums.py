from enum import StrEnum


class GoalUnit(StrEnum):
    POINTS = "points"
    QUALIFIED_HELD_MEETING = "qualified_held_meeting"


class PeriodType(StrEnum):
    YEAR = "year"
    QUARTER = "quarter"
    MONTH = "month"


class EventType(StrEnum):
    TOUCH_SENT = "touch_sent"
    REPLY_RECEIVED = "reply_received"
    POSITIVE_REPLY = "positive_reply"
    MEETING_BOOKED = "meeting_booked"
    MEETING_HELD = "meeting_held"
    MEETING_NO_SHOW = "meeting_no_show"
    MEETING_RESCHEDULED = "meeting_rescheduled"
    MEETING_CANCELLED = "meeting_cancelled"
    AD_ACCEPTED = "ad_accepted"
    AD_REJECTED = "ad_rejected"
    S1_REACHED = "s1_reached"
    S2_REACHED = "s2_reached"
    CREDIT_CLAWED_BACK = "credit_clawed_back"
    DORMANCY_REQUALIFIED = "dormancy_requalified"
    INVITE_ACCEPTED = "invite_accepted"
    INVITE_DECLINED = "invite_declined"
    OOO_AUTOREPLY = "ooo_autoreply"
    BOUNCE = "bounce"
    UNSUBSCRIBE = "unsubscribe"


class PersonaTier(StrEnum):
    GLOBAL_C_SUITE = "global_c_suite"
    VP_LEVEL = "vp_level"
    DIRECTOR = "director"
    MANAGER = "manager"
    IC = "ic"


class Channel(StrEnum):
    EMAIL = "email"
    CALL = "call"
    LINKEDIN = "linkedin"


class EventSource(StrEnum):
    CRM = "crm"
    EMAIL = "email"
    CALENDAR = "calendar"
    MANUAL = "manual"
    MOCK = "mock"


class RateMetric(StrEnum):
    REPLY_RATE = "reply_rate"
    POSITIVE_REPLY_RATE = "positive_reply_rate"
    BOOK_RATE = "book_rate"
    SHOW_RATE = "show_rate"
    QUALIFY_RATE = "qualify_rate"
    AD_ACCEPT_RATE = "ad_accept_rate"


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FunnelStage(StrEnum):
    CREATE = "create"
    CONVERT = "convert"
    HOLD = "hold"


class JobStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED_APPROVED = "edited_approved"
    WRITTEN_BACK = "written_back"
    EXPIRED = "expired"
    FAILED = "failed"
    SKIPPED = "skipped"


class ReplanReason(StrEnum):
    WEEKLY_CASCADE = "weekly_cascade"
    PACE_GAP = "pace_gap"
    RATE_DRIFT = "rate_drift"
    GOAL_EDITED = "goal_edited"
    CAPACITY_CHANGE = "capacity_change"


class VerdictResult(StrEnum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"

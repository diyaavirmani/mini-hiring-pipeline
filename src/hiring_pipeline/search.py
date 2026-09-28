"""Read-only candidate search with deterministic parsing and bounded AI fallback."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
import json
import math
import re
from difflib import SequenceMatcher
from typing import Callable, Dict, List, Optional, Set, Tuple
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .pipeline import Stage, get_candidate, list_candidates_grouped


STAGES = tuple(stage.value for stage in Stage)
STAGE_PATTERN = "|".join(re.escape(stage.casefold()) for stage in STAGES)
EXAMPLES = [
    "Find Priya Sharma",
    "Who's in Interview right now?",
    "Who has been stuck in Screening for more than a week?",
    "Who moved to Interview since Monday?",
    "Who reached Offer but didn't get hired?",
    "Everyone except rejected candidates.",
]
FUZZY_THRESHOLD = 0.62

NAME_STOP_WORDS = {
    "find", "search", "show", "who", "whose", "is", "are", "was", "were",
    "has", "have", "had", "been", "the", "a", "an", "candidate", "candidates",
    "person", "people", "everyone", "all", "in", "at", "of", "for", "right",
    "now", "currently", "stuck", "more", "than", "over", "above", "week", "weeks",
    "day", "days", "month", "months", "moved", "move", "to", "into", "since",
    "monday", "reached", "reach", "but", "did", "didnt", "get", "got", "hired",
    "except", "excluding", "exclude", "not", "with", "without", "and", "then",
    "please", "thanks", "thank",
    "offer", "applied", "screening", "interview", "rejected",
}


@dataclass
class SearchFilters:
    name_query: Optional[str] = None
    name_is_explicit: bool = False
    current_stage: Optional[str] = None
    excluded_stages: Set[str] = field(default_factory=set)
    minimum_days_in_stage: Optional[float] = None
    moved_to: Optional[str] = None
    moved_since_monday: bool = False
    reached_stage: Optional[str] = None
    not_hired: bool = False
    source: str = "rules"


class QueryNotUnderstood(ValueError):
    def __init__(self, message: str, examples=None):
        super().__init__(message)
        self.examples = examples or EXAMPLES


class SearchProviderUnavailable(RuntimeError):
    pass


def _normalized_query(query: str) -> str:
    return (
        query.casefold()
        .replace("’", "'")
        .replace("‘", "'")
        .replace("who's", "who is")
        .replace("didn't", "did not")
        .strip()
    )


def _name_tokens(value: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", value.casefold())


def _single_token_score(query: str, candidate: str) -> float:
    if query == candidate:
        return 1.0
    if len(query) >= 3 and query in candidate:
        return 0.92
    if len(query) >= 3 and candidate in query:
        return 0.88
    if min(len(query), len(candidate)) < 3:
        return 0.0
    return SequenceMatcher(None, query, candidate).ratio()


def name_match_score(query: str, full_name: str) -> float:
    """Return 0 for a non-match; otherwise a normalized typo-tolerant score."""
    query_tokens = _name_tokens(query)
    candidate_tokens = _name_tokens(full_name)
    if not query_tokens or not candidate_tokens:
        return 0.0
    if " ".join(query_tokens) == " ".join(candidate_tokens):
        return 1.0
    normalized_query = " ".join(query_tokens)
    normalized_name = " ".join(candidate_tokens)
    if normalized_query in normalized_name:
        return 0.96

    token_scores = [
        max(_single_token_score(token, name_token) for name_token in candidate_tokens)
        for token in query_tokens
    ]
    if any(score < FUZZY_THRESHOLD for score in token_scores):
        return 0.0
    return sum(token_scores) / len(token_scores)


def _extract_name(query: str) -> Tuple[Optional[str], bool]:
    normalized = _normalized_query(query)
    explicit = bool(re.match(r"^(?:find|search|show|look\s+up)\b", normalized))
    tokens = _name_tokens(normalized)
    remainder = [token for token in tokens if token not in NAME_STOP_WORDS]
    return (" ".join(remainder) or None), explicit


def parse_deterministic_query(
    query: str,
    known_names: List[str],
    now: Optional[datetime] = None,
    timezone_name: str = "Asia/Kolkata",
) -> Optional[SearchFilters]:
    """Parse the bounded search grammar; return None when rules cannot interpret it."""
    normalized = _normalized_query(query)
    if not normalized:
        return None
    filters = SearchFilters()

    exclude_match = re.search(
        r"\b(?:except|excluding|exclude|not\s+including|not\s+in)\s+(?:the\s+)?rejected\b",
        normalized,
    )
    if exclude_match:
        filters.excluded_stages.add(Stage.REJECTED.value)

    moved_matches = list(re.finditer(
        r"\bmoved\s+(?:to|into)\s+(" + STAGE_PATTERN + r")\b", normalized
    ))
    if len(moved_matches) > 1 or re.search(r"\bnot\s+since\s+monday\b", normalized):
        return None
    moved_match = moved_matches[0] if moved_matches else None
    if moved_match:
        filters.moved_to = _canonical_stage(moved_match.group(1))
        filters.moved_since_monday = bool(re.search(r"\bsince\s+monday\b", normalized))

    reached_offer = re.search(
        r"\breached\s+(?:the\s+)?offer\s+(?:(?:but|and)\s+)?"
        r"(?:didn['’]?t|did\s+not|not)\s+get\s+hired\b",
        normalized,
    )
    if reached_offer:
        filters.reached_stage = Stage.OFFER.value
        filters.not_hired = True

    current_matches = list(re.finditer(
        r"\b(?:in|at|currently\s+in|stuck\s+in|stuck\s+at)\s+(" + STAGE_PATTERN + r")\b",
        normalized,
    ))
    current_matches = [
        match for match in current_matches
        if not (exclude_match and exclude_match.start() <= match.start() < exclude_match.end())
        and not re.search(r"\b(?:not|never|except|excluding)\s+$", normalized[:match.start()])
    ]
    current_stages = {_canonical_stage(match.group(1)) for match in current_matches}
    if len(current_stages) > 1:
        return None
    if current_stages:
        filters.current_stage = next(iter(current_stages))

    duration_match = re.search(
        r"\b(?:more\s+than|over|longer\s+than)\s+(\d+|a|an|one)\s+(day|days|week|weeks|month|months)\b",
        normalized,
    )
    if duration_match and re.search(
        r"\bnot\s+(?:more\s+than|over|longer\s+than)\b", normalized
    ):
        return None
    if re.search(r"\bsince\b", normalized) and not re.search(r"\bsince\s+monday\b", normalized):
        return None
    if re.search(r"\bsince\s+monday\b", normalized) and not filters.moved_to:
        return None
    if duration_match:
        amount_text, unit = duration_match.groups()
        amount = 1 if amount_text in {"a", "an", "one"} else int(amount_text)
        multiplier = 1 if unit.startswith("day") else 7 if unit.startswith("week") else 30
        filters.minimum_days_in_stage = float(amount * multiplier)

    # Search-style prefixes make an unknown name a valid search with zero results.
    query_for_name = normalized
    if duration_match:
        query_for_name = (
            normalized[:duration_match.start()] + " " + normalized[duration_match.end():]
        )
    filters.name_query, filters.name_is_explicit = _extract_name(query_for_name)
    if filters.name_is_explicit and not filters.name_query and not any((
        filters.current_stage, filters.excluded_stages,
        filters.minimum_days_in_stage is not None, filters.moved_to,
        filters.reached_stage,
    )):
        prefix = re.match(r"^(?:find|search|show|look\s+up)\s+", normalized)
        filters.name_query = " ".join(_name_tokens(normalized[prefix.end():])) if prefix else None

    recognized_spans = [match for match in (
        exclude_match, moved_match, reached_offer, *current_matches
    ) if match is not None]
    for stage_match in re.finditer(r"\b(" + STAGE_PATTERN + r")\b", normalized):
        if not any(
            span.start() <= stage_match.start() and stage_match.end() <= span.end()
            for span in recognized_spans
        ):
            return None
    recognized_filter = any((
        filters.current_stage,
        filters.excluded_stages,
        filters.minimum_days_in_stage is not None,
        filters.moved_to,
        filters.reached_stage,
    ))
    if filters.name_query:
        if filters.name_is_explicit:
            return filters
        if any(name_match_score(filters.name_query, name) >= FUZZY_THRESHOLD for name in known_names):
            return filters
        return None
    if recognized_filter:
        return filters
    return None


def _canonical_stage(stage: str) -> str:
    for valid_stage in STAGES:
        if valid_stage.casefold() == stage.casefold():
            return valid_stage
    raise ValueError("Unrecognized pipeline stage.")


def validate_ai_filters(raw: dict) -> SearchFilters:
    """Validate Gemini's filter-only interpretation against strict allowlists."""
    if not isinstance(raw, dict):
        raise ValueError("AI search interpretation must be an object.")
    allowed = {
        "understood", "name_query", "current_stage", "excluded_stages",
        "minimum_days_in_stage", "moved_to", "moved_since_monday",
        "reached_stage", "not_hired",
    }
    if set(raw) != allowed:
        raise ValueError("AI search interpretation has missing or unknown fields.")
    if type(raw["understood"]) is not bool:
        raise ValueError("AI search interpretation has an invalid understood flag.")
    if not raw["understood"]:
        raise QueryNotUnderstood("I couldn't understand that search query.")

    def optional_stage(field_name: str) -> Optional[str]:
        value = raw[field_name]
        if value == "":
            return None
        if not isinstance(value, str) or value not in STAGES:
            raise ValueError("AI search interpretation contains an invalid stage.")
        return value

    name = raw["name_query"]
    if not isinstance(name, str) or len(name) > 120:
        raise ValueError("AI search interpretation contains an invalid name.")
    excluded = raw["excluded_stages"]
    if not isinstance(excluded, list) or any(stage not in STAGES for stage in excluded):
        raise ValueError("AI search interpretation contains invalid exclusions.")
    duration = raw["minimum_days_in_stage"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("AI search interpretation contains an invalid duration.")
    if not math.isfinite(float(duration)) or duration < 0 or duration > 36500:
        raise ValueError("AI search duration must be between 0 and 36500 days.")
    since = raw["moved_since_monday"]
    not_hired = raw["not_hired"]
    if type(since) is not bool or type(not_hired) is not bool:
        raise ValueError("AI search interpretation contains invalid flags.")
    moved_to = optional_stage("moved_to")
    reached_stage = optional_stage("reached_stage")
    if since and moved_to is None:
        raise ValueError("A Monday filter requires a moved-to stage.")
    if not_hired and reached_stage is None:
        raise ValueError("A not-hired filter requires a reached stage.")
    if not (name.strip() or optional_stage("current_stage") or excluded or duration or moved_to or reached_stage):
        raise QueryNotUnderstood("I couldn't identify any candidate search filters.")

    return SearchFilters(
        name_query=name.strip() or None,
        name_is_explicit=True,
        current_stage=optional_stage("current_stage"),
        excluded_stages=set(excluded),
        minimum_days_in_stage=float(duration) if duration else None,
        moved_to=moved_to,
        moved_since_monday=since,
        reached_stage=reached_stage,
        not_hired=not_hired,
        source="ai",
    )


class GeminiQueryInterpreter:
    """Use Gemini only to map unknown prose to a restricted filter schema."""

    def __init__(self, api_key: str, model: str = "gemini-3.8-flash", timeout: float = 12):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def __call__(self, query: str) -> SearchFilters:
        stage_schema = {"type": "string", "enum": [""] + list(STAGES)}
        schema = {
            "type": "object",
            "properties": {
                "understood": {"type": "boolean"},
                "name_query": {"type": "string"},
                "current_stage": stage_schema,
                "excluded_stages": {
                    "type": "array", "items": {"type": "string", "enum": list(STAGES)}
                },
                "minimum_days_in_stage": {"type": "number"},
                "moved_to": stage_schema,
                "moved_since_monday": {"type": "boolean"},
                "reached_stage": stage_schema,
                "not_hired": {"type": "boolean"},
            },
            "required": [
                "understood", "name_query", "current_stage", "excluded_stages",
                "minimum_days_in_stage", "moved_to", "moved_since_monday",
                "reached_stage", "not_hired",
            ],
        }
        prompt = (
            "Interpret the user's candidate search as filters only. Do not invent candidate data, "
            "execute actions, or output SQL. Supported filters are candidate name, current stage, "
            "excluded current stages, minimum days in current stage, moved-to stage since this "
            "Monday, and reached stage without a later Hired event. If the query is not a "
            "candidate search or cannot be represented by these filters, set understood=false. "
            "Use empty strings, empty arrays, zero, and false for absent values.\n"
            "Query: " + query
        )
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            + quote(self.model, safe="")
            + ":generateContent"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": 512,
                "responseFormat": {
                    "text": {"mimeType": "application/json", "schema": schema}
                },
            },
        }
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            return validate_ai_filters(json.loads(text))
        except QueryNotUnderstood:
            raise
        except (URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise SearchProviderUnavailable(
                "AI search interpretation is temporarily unavailable. Try one of the supported examples."
            ) from exc


def _monday_start(now: datetime, timezone_name: str) -> datetime:
    local_now = now.astimezone(ZoneInfo(timezone_name))
    monday = local_now.date() - timedelta(days=local_now.weekday())
    return datetime.combine(monday, time.min, tzinfo=local_now.tzinfo).astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _candidate_match(
    candidate: dict,
    filters: SearchFilters,
    monday_start: datetime,
    now: datetime,
) -> Tuple[bool, float, Optional[datetime]]:
    if filters.current_stage and candidate["current_stage"] != filters.current_stage:
        return False, 0.0, None
    if candidate["current_stage"] in filters.excluded_stages:
        return False, 0.0, None
    elapsed_seconds = max(
        0.0,
        (now.astimezone(timezone.utc) - _parse_timestamp(candidate["stage_entered_at"])).total_seconds(),
    )
    if filters.minimum_days_in_stage is not None:
        if elapsed_seconds <= filters.minimum_days_in_stage * 86400:
            return False, 0.0, None

    history = candidate["history"]
    if filters.reached_stage and not any(
        event["to_stage"] == filters.reached_stage for event in history
    ):
        return False, 0.0, None
    if filters.not_hired and any(event["to_stage"] == Stage.HIRED.value for event in history):
        return False, 0.0, None

    moved_event = None
    if filters.moved_to:
        moved = [event for event in history if event["to_stage"] == filters.moved_to]
        if filters.moved_since_monday:
            moved = [event for event in moved if _parse_timestamp(event["created_at"]) >= monday_start]
        if not moved:
            return False, 0.0, None
        moved_event = max(moved, key=lambda event: event["id"])

    score = 1.0
    if filters.name_query:
        score = name_match_score(filters.name_query, candidate["full_name"])
        if score < FUZZY_THRESHOLD:
            return False, score, None
    event_time = _parse_timestamp(moved_event["created_at"]) if moved_event else None
    return True, score, event_time


def _explanation(filters: SearchFilters) -> str:
    parts = []
    if filters.name_query:
        parts.append("name matches " + filters.name_query)
    if filters.current_stage:
        parts.append("currently in " + filters.current_stage)
    if filters.minimum_days_in_stage is not None:
        parts.append("in the current stage for more than {} days".format(filters.minimum_days_in_stage))
    if filters.moved_to:
        if filters.moved_since_monday:
            parts.append("moved to {} since Monday".format(filters.moved_to))
        else:
            parts.append("has moved to {}".format(filters.moved_to))
    if filters.reached_stage:
        parts.append("reached {}".format(filters.reached_stage))
    if filters.not_hired:
        parts.append("has no Hired event")
    if filters.excluded_stages:
        parts.append("excluding current stage {}".format(", ".join(sorted(filters.excluded_stages))))
    return " and ".join(parts) or "candidate search"


def search_candidates(
    query: str,
    database_path,
    timezone_name: str = "Asia/Kolkata",
    now: Optional[datetime] = None,
    ai_interpreter: Optional[Callable[[str], SearchFilters]] = None,
) -> Dict[str, object]:
    """Search current candidates and immutable histories without writing data."""
    if not query.strip():
        raise QueryNotUnderstood("Enter a search query.")
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("Search clock must be timezone-aware.")
    grouped = list_candidates_grouped(database_path)
    candidates = [candidate for members in grouped.values() for candidate in members]
    known_names = [candidate["full_name"] for candidate in candidates]
    filters = parse_deterministic_query(query, known_names, moment, timezone_name)
    source = "rules"
    if filters is None:
        if ai_interpreter is None:
            raise QueryNotUnderstood(
                "I couldn't understand that search. Try a candidate name, a current stage, "
                "time in stage, movement since Monday, an Offer outcome, or an exclusion."
            )
        try:
            filters = ai_interpreter(query)
        except QueryNotUnderstood:
            raise
        except Exception as exc:
            raise SearchProviderUnavailable(
                "AI search interpretation is temporarily unavailable. Try a supported search example."
            ) from exc
        if not isinstance(filters, SearchFilters):
            raise SearchProviderUnavailable("AI search returned invalid filters.")
        try:
            filters = validate_ai_filters({
                "understood": True,
                "name_query": filters.name_query or "",
                "current_stage": filters.current_stage or "",
                "excluded_stages": list(filters.excluded_stages),
                "minimum_days_in_stage": filters.minimum_days_in_stage or 0,
                "moved_to": filters.moved_to or "",
                "moved_since_monday": filters.moved_since_monday,
                "reached_stage": filters.reached_stage or "",
                "not_hired": filters.not_hired,
            })
        except QueryNotUnderstood:
            raise
        except Exception as exc:
            raise SearchProviderUnavailable("AI search returned invalid filters.") from exc
        source = "ai"

    monday_start = _monday_start(moment, timezone_name)
    results = []
    for listed in candidates:
        candidate = get_candidate(listed["id"], database_path)
        match, score, moved_time = _candidate_match(candidate, filters, monday_start, moment)
        if not match:
            continue
        result = {
            key: candidate[key]
            for key in (
                "id", "full_name", "email", "current_stage", "stage_entered_at",
                "time_in_current_stage_seconds", "time_in_current_stage_days",
            )
        }
        elapsed_seconds = max(
            0.0,
            (moment.astimezone(timezone.utc) - _parse_timestamp(candidate["stage_entered_at"])).total_seconds(),
        )
        result["time_in_current_stage_seconds"] = elapsed_seconds
        result["time_in_current_stage_days"] = elapsed_seconds / 86400
        result["match_score"] = round(score, 4)
        if moved_time is not None:
            result["matched_movement_at"] = moved_time.isoformat().replace("+00:00", "Z")
        results.append(result)

    if filters.name_query:
        results.sort(key=lambda item: (-item["match_score"], item["full_name"].casefold()))
    elif filters.minimum_days_in_stage is not None:
        results.sort(key=lambda item: (-item["time_in_current_stage_seconds"], item["full_name"].casefold()))
    elif filters.moved_to:
        results.sort(key=lambda item: item["full_name"].casefold())
        results.sort(key=lambda item: item.get("matched_movement_at", ""), reverse=True)
    else:
        results.sort(key=lambda item: item["full_name"].casefold())

    return {
        "query": query,
        "interpretation_source": source,
        "explanation": "Understood as: " + _explanation(filters) + ".",
        "count": len(results),
        "results": results,
    }

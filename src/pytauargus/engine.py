"""High-level Tau-Argus engine driving the native C++ bindings.

Usage:
    from pytauargus.engine import Engine, parse_rda, run_batch
    eng = Engine()
    eng.run_batch("data/TestRecode.arb")

Or drive the native engine manually:
    eng = Engine()
    meta = parse_rda("data/tau_testW.rda")
    eng.open_microdata("data/tau_testW.asc", meta)
"""

from __future__ import annotations

import itertools
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from ._tauargus import HiTaSCtrl, RounderCtrl, TauArgus, audit_jj
from .batch import (
    BatchError,
    Command,
    OpenMicrodata,
    OpenTableData,
    OpenMetadata,
    SpecifyTable,
    SafetyRule,
    ReadMicrodata,
    ReadTable,
    Apriory,
    Cover,
    Suppress,
    WriteTable,
    Recode,
    Solver,
    GoInteractive,
    Logbook,
    VersionInfo,
    Anco,
    Clear,
    Tokenizer,
    parse_batch,
)

logger = logging.getLogger(__name__)


# WriteCSV/WriteCSVTable index into DimSequence[d] for each table dimension.
# The native code dereferences it (a null/empty sequence segfaults), so the
# identity sequence {0..9} (Java SaveTable.MAXDIM) is the correct default.
IDENTITY_DIM_SEQUENCE = list(range(10))


# ===========================================================================
# Tabular (pre-aggregated) table-input constants
# ===========================================================================
# Cell status values — must match Java CellStatus / native CS_* (defines.h).
CS_UNKNOWN = 0
CS_SAFE = 1
CS_SAFE_MANUAL = 2
CS_UNSAFE_RULE = 3
CS_UNSAFE_MANUAL = 9
CS_PROTECT_MANUAL = 10
CS_SECONDARY_UNSAFE = 11
CS_EMPTY = 14

# Apriori change-type sentinels (Java ``AP_ADJUST_*`` in APriori.java).
_AP_ADJUST_COST = -1
_AP_ADJUST_PROT_LEVEL = -2
_AP_ADJUST_APRIORI_BOUND = -3

# Sentinel for an "unparsed" numeric field (Java Cell.UNKNOWN).
_CELL_UNKNOWN = -999999999

# Native error codes returned by SetInCodeList/SetInTable (see defines.h).
_ERR_CODENOTINCODELIST = 1017
_ERR_CELLALREADYFILLED = 1022
_ERR_CODEDOESNOTEXIST = 1027

# Table additivity modes — must match Java ``TableSet.ADDITIVITY_*``.
# 0 = check additivity, 1 = recompute marginals, 2 = not required (cover table).
_ADDITIVITY_CHECK = 0
_ADDITIVITY_RECOMPUTE = 1
_ADDITIVITY_NOT_REQUIRED = 2


def _status_symbol(status: int) -> str:
    """Intermediate-format status symbol (Java ``CellStatus``/``Category``).

    ``isEmpty`` (13/14) -> ``E``; else the category symbol: 1,2 -> ``S``;
    3-9 -> ``U``; 10 -> ``P``; 11,12 -> ``M``; anything else -> ``?``.
    """
    if status in (CS_EMPTY_NONSTRUCT, CS_EMPTY):
        return "E"
    if status in (CS_SAFE, CS_SAFE_MANUAL):
        return "S"
    if 3 <= status <= 9:
        return "U"
    if status == CS_PROTECT_MANUAL:
        return "P"
    if status in (CS_SECONDARY_UNSAFE, CS_SECONDARY_UNSAFE_MANUAL):
        return "M"
    return "?"


# ``CS_EMPTY_NONSTRUCT`` / ``CS_SECONDARY_UNSAFE_MANUAL`` (see defines.h).
CS_EMPTY_NONSTRUCT = 13
CS_SECONDARY_UNSAFE_MANUAL = 12


# ===========================================================================
# Variable dataclass (parsed from .rda)
# ===========================================================================
@dataclass
class Variable:
    name: str
    index: int = 0
    b_pos: int = 1
    var_len: int = 0
    n_decimals: int = 0
    in_table: bool = False
    missing: List[str] = field(default_factory=lambda: ["", ""])
    tot_code: str = ""
    # type (mirrors Java Type enum)
    type: str = "CAT_RESP"  # CATEGORICAL, RESPONSE, CAT_RESP, WEIGHT, HOLDING,
                            # REQUEST, SHADOW, COST, FREQUENCY, TOP_N,
                            # LOWER_PROTECTION_LEVEL, UPPER_PROTECTION_LEVEL,
                            # STATUS, RECORD_KEY
    # categorical fields
    has_distance_function: bool = False
    distance_function: List[int] = field(default_factory=lambda: [0] * 5)
    code_list_file: str = ""
    hierarchical: int = 0  # 0=NONE, 1=LEVELS, 2=FILE
    hier_levels: List[int] = field(default_factory=list)
    hier_levels_sum: int = 0
    hier_file_name: str = ""
    leading_string: str = "."
    # CKM / record-key
    p_table_file: str = ""
    p_table_file_cont: str = ""
    p_table_file_sep: str = ""
    ckm_type: str = "N"
    ckm_top_k: int = 1
    ckm_scaling: str = ""
    ckm_sigma0: float = -1
    ckm_sigma1: float = -1
    ckm_xstar: float = -1
    ckm_q: float = -1
    ckm_epsilon: List[float] = field(default_factory=list)
    ckm_separation: bool = False
    ckm_zeros: bool = False
    ckm_parity: bool = False
    # request
    request_code: List[str] = field(default_factory=lambda: ["", ""])
    # misc
    recoded: bool = False
    truncatable: bool = False

    @property
    def is_categorical(self) -> bool:
        return self.type in ("CATEGORICAL", "CAT_RESP")

    @property
    def is_response(self) -> bool:
        return self.type in ("RESPONSE", "CAT_RESP")

    @property
    def is_numeric(self) -> bool:
        # A variable is either categorical or numeric (native SetVariable):
        # CATEGORICAL/CAT_RESP are NOT numeric (the native would then parse
        # their codes with ConvertNumeric and fail with ISNOTNUMERIC).
        return self.type in (
            "RESPONSE", "WEIGHT", "SHADOW", "COST",
            "FREQUENCY", "TOP_N",
            "LOWER_PROTECTION_LEVEL", "UPPER_PROTECTION_LEVEL",
            "RECORD_KEY",
        )

    @property
    def is_weight(self) -> bool:
        return self.type == "WEIGHT"

    @property
    def is_holding(self) -> bool:
        return self.type == "HOLDING"

    @property
    def is_request(self) -> bool:
        return self.type == "REQUEST"

    @property
    def is_record_key(self) -> bool:
        return self.type == "RECORD_KEY"

    def n_missings(self) -> int:
        n = 0
        while n < 2 and self.missing[n]:
            n += 1
        return n

    def get_total_code(self) -> str:
        return self.tot_code if self.tot_code else "Total"

    def is_total_code(self, code: str) -> bool:
        """Case-insensitive total-code test (Java ``isTotalCode``)."""
        return code.lower() == (self.tot_code or "").lower()

    def is_missing(self, code: str) -> bool:
        for m in self.missing:
            if m and code == m:
                return True
        return False

    def normalise_code(self, code: str) -> str:
        """Pad a non-hierarchical code to ``var_len`` (Java ``normaliseCode``)."""
        if code and self.hierarchical == 0:
            return self.pad_code(code)
        return code

    def pad_code(self, code: str) -> str:
        return code.rjust(self.var_len) if self.var_len else code


@dataclass
class Metadata:
    variables: List[Variable] = field(default_factory=list)
    data_file: str = ""
    meta_file: str = ""
    field_separator: str = ";"
    is_table: bool = False
    safe_status: str = "S"
    unsafe_status: str = "U"
    protect_status: str = "P"

    def find(self, name: str) -> Optional[Variable]:
        for v in self.variables:
            if v.name.lower() == name.lower():
                return v
        return None

    def index_of(self, name: str) -> int:
        for i, v in enumerate(self.variables):
            if v.name.lower() == name.lower():
                return i
        return -1

    def contains(self, vtype: str) -> bool:
        return any(v.type == vtype for v in self.variables)


# ===========================================================================
# .rda parser
# ===========================================================================
def parse_rda(path: Union[str, Path]) -> Metadata:
    """Parse a legacy Tau-Argus ``.rda`` metadata file."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    tok = Tokenizer(text)
    meta = Metadata(meta_file=str(path))
    variable: Optional[Variable] = None
    ckmspecified = False

    while tok.next_line() is not None:
        token = tok.next_token()
        if token == "":
            continue

        if token.startswith("<"):
            if token == "<SEPARATOR>" and tok.line_number == 1:
                meta.field_separator = tok.next_token()
                continue
            if token == "<SPSS>" and tok.line_number == 1:
                continue

            if variable is None:
                raise BatchError("A variable should be declared before giving its properties")

            # -- variable property tags --------------------------------------
            if token in ("<RECODEABLE>", "<RECODABLE>"):
                if variable.type == "RESPONSE":
                    variable.type = "CAT_RESP"
                else:
                    variable.type = "CATEGORICAL"
            elif token == "<TOTCODE>":
                variable.tot_code = tok.next_token()
            elif token == "<DISTANCE>":
                variable.has_distance_function = True
                for i in range(5):
                    t = tok.next_token()
                    if t == "":
                        variable.distance_function[i] = 1 if i == 0 else variable.distance_function[i - 1]
                    else:
                        variable.distance_function[i] = int(t)
            elif token == "<CODELIST>":
                variable.code_list_file = tok.next_token()
            elif token == "<HIERARCHICAL>":
                variable.hierarchical = 2  # HIER_FILE
            elif token == "<HIERLEVELS>":
                variable.hierarchical = 1  # HIER_LEVELS
                x = 0
                while True:
                    t = tok.next_token()
                    if t == "":
                        break
                    n = int(t)
                    variable.hier_levels.append(n)
                    x += n
                variable.hier_levels_sum = x
            elif token == "<HIERLEADSTRING>":
                variable.leading_string = tok.next_token()
            elif token == "<HIERCODELIST>":
                variable.hier_file_name = tok.next_token()
            elif token == "<NUMERIC>":
                hs = tok.next_token()
                if hs == "":
                    if variable.type == "CATEGORICAL":
                        variable.type = "CAT_RESP"
                    else:
                        variable.type = "RESPONSE"
                elif hs == "<SHADOW>":
                    variable.type = "SHADOW"
                elif hs in ("<COST>", "<COSTVAR>"):
                    variable.type = "COST"
                elif hs == "<LOWERPL>":
                    variable.type = "LOWER_PROTECTION_LEVEL"
                elif hs == "<UPPERPL>":
                    variable.type = "UPPER_PROTECTION_LEVEL"
            elif token == "<WEIGHT>":
                variable.type = "WEIGHT"
            elif token == "<HOLDING>":
                variable.type = "HOLDING"
            elif token == "<REQUEST>":
                variable.type = "REQUEST"
                variable.request_code = [tok.next_token(), tok.next_token()]
            elif token == "<DECIMALS>":
                variable.n_decimals = int(tok.next_token())
            elif token == "<TRUNCABLE>":
                variable.truncatable = True
            elif token == "<RECORDKEY>":
                variable.type = "RECORD_KEY"
            elif token in ("<PFILE_FREQ>", "<PFILE>"):
                variable.p_table_file = tok.next_token()
            elif token == "<PFILE_CONT>":
                variable.p_table_file_cont = tok.next_token()
            elif token == "<PFILE_SEP>":
                variable.p_table_file_sep = tok.next_token()
            elif token == "<CKM>":
                if not variable.is_response:
                    raise BatchError("<CKM> tag only allowed for numeric variables.")
                hs = tok.next_field("(").upper()
                ckmspecified = True
                if hs == "N":
                    ckmspecified = False
                elif hs in ("M", "D", "V"):
                    variable.ckm_type = hs
                elif hs == "T":
                    variable.ckm_type = hs
                    variable.ckm_top_k = int(tok.next_field(")"))
                else:
                    raise BatchError(f"Unknown type <CKM> {hs} for variable {variable.name}")
                if ckmspecified:
                    variable.ckm_epsilon = [1.0] * variable.ckm_top_k
            elif token == "<INCLUDEZEROS>":
                if not ckmspecified:
                    raise BatchError("<INCLUDEZEROS> only allowed after <CKM>.")
                variable.ckm_zeros = tok.next_token() == "Y"
            elif token == "<PARITY>":
                if not ckmspecified:
                    raise BatchError("<PARITY> only allowed after <CKM>.")
                variable.ckm_parity = tok.next_token() == "Y"
            elif token == "<SEPARATION>":
                if not ckmspecified:
                    raise BatchError("<SEPARATION> only allowed after <CKM>.")
                variable.ckm_separation = tok.next_token() == "Y"
            elif token == "<SCALING>":
                if not ckmspecified:
                    raise BatchError("<SCALING> only allowed after <CKM>.")
                hs = tok.next_field("(").upper()
                variable.ckm_scaling = hs
                if hs == "F":
                    variable.ckm_sigma0 = float(tok.next_field(","))
                    variable.ckm_sigma1 = float(tok.next_field(","))
                    variable.ckm_xstar = float(tok.next_field(","))
                    t = variable.ckm_top_k
                    if t >= 2:
                        variable.ckm_q = float(tok.next_field(","))
                        for i in range(2, t):
                            variable.ckm_epsilon[i - 1] = float(tok.next_field(","))
                        variable.ckm_epsilon[t - 1] = float(tok.next_field(")"))
                    else:
                        variable.ckm_q = float(tok.next_field(")"))
                elif hs == "N":
                    t = variable.ckm_top_k
                    if t >= 2:
                        variable.ckm_sigma1 = float(tok.next_field(","))
                        for i in range(2, t):
                            variable.ckm_epsilon[i - 1] = float(tok.next_field(","))
                        variable.ckm_epsilon[t - 1] = float(tok.next_field(")"))
                    else:
                        variable.ckm_sigma1 = float(tok.next_field(")"))
                else:
                    raise BatchError(f"Unknown type <SCALING> {hs} for variable {variable.name}")
            else:
                raise BatchError(f"Unknown keyword ({token}) in line {tok.line_number}")
        else:
            # -- new variable declaration ------------------------------------
            variable = Variable(name=token, index=len(meta.variables))
            meta.variables.append(variable)
            ckmspecified = False
            variable.b_pos = int(tok.next_token())
            variable.var_len = int(tok.next_token())
            variable.missing = [
                _normalise_missing(tok.next_token(), variable.var_len),
                _normalise_missing(tok.next_token(), variable.var_len),
            ]

    tok.close()
    return meta


def parse_rda_table(path: Union[str, Path]) -> Metadata:
    """Parse a tabular (pre-aggregated) ``.rda`` metadata file.

    Mirrors ``Metadata.readTableMetadata``. Variable declarations carry no
    ``bPos``/``varLen`` (only name + two missing values); ``<NUMERIC>`` may be
    followed by a subtype (``<SHADOW>``/``<COSTVAR>``/``<LOWERPL>``/
    ``<UPPERPL>``), and top-level ``<SAFE>``/``<UNSAFE>``/``<PROTECT>`` tags
    set the status characters.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    tok = Tokenizer(text)
    meta = Metadata(meta_file=str(path), is_table=True)
    meta.field_separator = ";"
    variable: Optional[Variable] = None

    while tok.next_line() is not None:
        token = tok.next_token()
        if token == "":
            continue

        if token.startswith("<"):
            if token == "<SEPARATOR>":
                meta.field_separator = _unquote(tok.next_token())
                continue
            if token == "<SAFE>":
                meta.safe_status = _unquote(tok.next_token())
                continue
            if token == "<UNSAFE>":
                meta.unsafe_status = _unquote(tok.next_token())
                continue
            if token == "<PROTECT>":
                meta.protect_status = _unquote(tok.next_token())
                continue

            if variable is None:
                raise BatchError("A variable should be declared before giving its properties")

            if token in ("<RECODEABLE>", "<RECODABLE>"):
                variable.type = "CATEGORICAL"
                variable.in_table = True
            elif token == "<TOTCODE>":
                variable.tot_code = _unquote(tok.next_token())
            elif token == "<DISTANCE>":
                variable.has_distance_function = True
                for i in range(5):
                    t = tok.next_token()
                    if t == "":
                        variable.distance_function[i] = 1 if i == 0 else variable.distance_function[i - 1]
                    else:
                        variable.distance_function[i] = int(t)
            elif token == "<CODELIST>":
                variable.code_list_file = _unquote(tok.next_token())
            elif token == "<HIERARCHICAL>":
                variable.hierarchical = 2
            elif token == "<HIERLEVELS>":
                variable.hierarchical = 1
                x = 0
                while True:
                    t = tok.next_token()
                    if t == "":
                        break
                    n = int(t)
                    variable.hier_levels.append(n)
                    x += n
                variable.hier_levels_sum = x
            elif token == "<HIERLEADSTRING>":
                variable.leading_string = _unquote(tok.next_token())
            elif token == "<HIERCODELIST>":
                variable.hier_file_name = _unquote(tok.next_token())
            elif token == "<NUMERIC>":
                sub = tok.next_token()
                if sub == "":
                    variable.type = "RESPONSE"
                elif sub == "<SHADOW>":
                    variable.type = "SHADOW"
                elif sub in ("<COST>", "<COSTVAR>"):
                    variable.type = "COST"
                elif sub == "<LOWERPL>":
                    variable.type = "LOWER_PROTECTION_LEVEL"
                elif sub == "<UPPERPL>":
                    variable.type = "UPPER_PROTECTION_LEVEL"
            elif token == "<FREQUENCY>":
                variable.type = "FREQUENCY"
            elif token == "<MAXSCORE>":
                variable.type = "TOP_N"
            elif token == "<STATUS>":
                variable.type = "STATUS"
            elif token == "<DECIMALS>":
                variable.n_decimals = int(tok.next_token())
            else:
                raise BatchError(f"Unknown keyword ({token}) in line {tok.line_number}")
        else:
            # -- new variable declaration (no bPos/varLen) --------------------
            variable = Variable(name=token, index=len(meta.variables))
            meta.variables.append(variable)
            variable.missing = [
                _normalise_missing(tok.next_token(), 0),
                _normalise_missing(tok.next_token(), 0),
            ]

    tok.close()
    return meta


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


def _normalise_missing(missing: str, var_len: int) -> str:
    if missing.strip() == "":
        return ""
    if var_len and len(missing) > var_len:
        raise BatchError(f"Missing value ({missing}) too long")
    return missing.rjust(var_len) if var_len else missing


# ===========================================================================
# Safety rule aggregation (per-table)
# ===========================================================================
@dataclass
class SafetyRuleSet:
    """Aggregated safety rules for a single table (maps to native SetTableSafety)."""
    dom_rule: bool = False
    dom_n: List[int] = field(default_factory=list)
    dom_k: List[int] = field(default_factory=list)
    pq_rule: bool = False
    pq_p: List[int] = field(default_factory=list)
    pq_q: List[int] = field(default_factory=list)
    pq_n: List[int] = field(default_factory=list)
    min_freq: List[int] = field(default_factory=list)
    freq_marge: List[int] = field(default_factory=list)
    peep_rule: bool = False
    peep_percentage: List[int] = field(default_factory=list)
    peep_marge: List[int] = field(default_factory=list)
    peep_min_freq: List[int] = field(default_factory=list)
    apply_peep: bool = False
    apply_weight: bool = False
    weight_on_safety: bool = False
    apply_holding: bool = False
    zero_rule: bool = False
    zero_range: float = 0.0
    empty_as_ns: bool = False
    ns_range: int = 10
    # Legacy TableSet.manualMarge = 20 (manual p.123: "The default value = 20"):
    # protection margin for manually unsafe cells when no MAN rule is given.
    manual_perc: int = 20

    @staticmethod
    def from_rules(rules: List[SafetyRule]) -> "SafetyRuleSet":
        """Build a SafetyRuleSet from the parsed batch SafetyRule objects."""
        s = SafetyRuleSet()
        n_pq = 0
        n_dom = 0
        n_freq = 0
        n_req = 0

        for rule in rules:
            args = [int(a) for a in rule.args]
            if rule.kind == "P":
                if n_pq >= 4:
                    raise BatchError("More than 4 P rules specified.")
                if len(args) == 2:
                    s.pq_p.append(args[0])
                    s.pq_q.append(100)
                    s.pq_n.append(args[1])
                elif len(args) == 3:
                    s.pq_p.append(args[0])
                    s.pq_q.append(args[1])
                    s.pq_n.append(args[2])
                else:
                    raise BatchError("P rule needs 2 or 3 parameters.")
                s.pq_rule = True
                if n_pq >= 2 and s.pq_p[n_pq] > 0:
                    s.apply_holding = True
                n_pq += 1
            elif rule.kind == "NK":
                if n_dom >= 4:
                    raise BatchError("More than 4 NK rules specified.")
                if len(args) != 2:
                    raise BatchError("NK rule needs 2 parameters.")
                s.dom_n.append(args[0])
                s.dom_k.append(args[1])
                s.dom_rule = True
                if n_dom >= 2:
                    s.apply_holding = True
                n_dom += 1
            elif rule.kind == "FREQ":
                if n_freq >= 2:
                    raise BatchError("More than 2 frequency rules specified.")
                if len(args) != 2:
                    raise BatchError("FREQ rule needs 2 parameters.")
                s.min_freq.append(args[0])
                s.freq_marge.append(args[1])
                if n_freq >= 1 and s.min_freq[n_freq] > 0:
                    s.apply_holding = True
                n_freq += 1
            elif rule.kind == "ZERO":
                if len(args) != 1:
                    raise BatchError("ZERO rule needs 1 parameter.")
                s.zero_range = float(args[0])
                s.zero_rule = True
            elif rule.kind == "WGT":
                if len(args) != 1:
                    raise BatchError("WGT needs 1 parameter.")
                k = args[0]
                if k < 0 or k > 3:
                    raise BatchError("WGT value should be 0-3.")
                s.apply_weight = k != 0
                s.weight_on_safety = k >= 2
            elif rule.kind == "MIS":
                # missingIsSafe — handled at set_table level, not safety
                pass
            elif rule.kind == "MAN":
                if len(args) != 1:
                    raise BatchError("MAN needs 1 parameter.")
                s.manual_perc = args[0]
            elif rule.kind == "REQ":
                if n_req >= 2:
                    raise BatchError("More than 2 request rules specified.")
                if len(args) != 4:
                    raise BatchError("REQ rule needs 4 parameters.")
                s.peep_percentage.extend([args[0], args[1]])
                s.peep_marge.append(args[3])
                s.peep_min_freq.append(args[2])
                if args[0] > 0 or args[2] > 0:
                    s.apply_peep = True
                if n_req >= 0 and args[0] > 0:
                    s.apply_holding = True
                n_req += 1
            else:
                raise BatchError(f"Unknown safety rule kind: {rule.kind}")

        return s


# ===========================================================================
# Engine
# ===========================================================================
class Engine:
    """High-level driver for the native Tau-Argus engine."""

    def __init__(self) -> None:
        self._tau = TauArgus()
        self._hitas = HiTaSCtrl()
        self._rounder = RounderCtrl()
        self._metadata: Optional[Metadata] = None
        self._data_file: str = ""
        self._is_table: bool = False
        self._tables: List[tuple] = []  # (SpecifyTable, SafetyRuleSet)
        self._safety_rules_buf: List[List[SafetyRule]] = []
        self._n_tables: int = 0
        self._work_dir: Path = Path.cwd()
        self._rounded_tables: set = set()  # indices of tables marked rounded (RND)
        self._ckm_tables: set = set()  # indices of tables marked cell-key protected
        self._protect_cover_table: bool = False  # legacy <COVER> global flag
        # Cell audit (intervalle) results: tab index -> {cell: (min, max, unsafe)}
        self._audit: Dict[int, Dict[int, Tuple[float, float, bool]]] = {}

    # -- property accessors --------------------------------------------------
    @property
    def version(self) -> str:
        return self._tau.version()

    @property
    def metadata(self) -> Optional[Metadata]:
        return self._metadata

    # -- microdata flow ------------------------------------------------------
    def open_microdata(self, data_file: str, meta: Metadata) -> None:
        """Set up native engine for a fixed-width microdata file.

        Does not reset registered tables (``_tables``); call this after the
        table/safety commands have been buffered during a batch run.
        """
        self._data_file = data_file
        self._metadata = meta
        meta.data_file = data_file

        self._tau.clean_all()
        self._tau.set_in_file_info(True, "")
        self._tau.set_number_var(len(meta.variables))

        for var in meta.variables:
            self._set_variable(var)

        # Explore the data file to build code lists
        ok, err, line, nvar = self._tau.explore_file(data_file)
        if not ok:
            raise BatchError(f"ExploreFile failed: {self._tau.error_string(err)} line {line}")

    def _set_variable(self, var: Variable) -> None:
        n_miss = var.n_missings()
        self._tau.set_variable(
            index=var.index,
            b_pos=var.b_pos,
            n_pos=var.var_len,
            n_dec=var.n_decimals,
            n_missing=n_miss,
            missing1=var.missing[0] if n_miss >= 1 else "",
            missing2=var.missing[1] if n_miss >= 2 else "",
            total_code=var.get_total_code(),
            is_peeper=var.is_request,
            peeper_code1=var.request_code[0] if var.is_request else "",
            peeper_code2=var.request_code[1] if var.is_request else "",
            is_categorical=var.is_categorical,
            is_numeric=var.is_numeric,
            is_weight=var.is_weight,
            is_hierarchical=var.hierarchical != 0,
            is_holding=var.is_holding,
            is_record_key=var.is_record_key,
        )
        if var.hierarchical == 2:  # HIER_FILE
            hier_path = self._resolve_path(var.hier_file_name)
            self._tau.set_hierarchical_codelist(var.index, str(hier_path), var.leading_string)
        elif var.hierarchical == 1:  # HIER_LEVELS
            # Trim trailing zero levels (Java counts up to the last non-zero).
            levels = var.hier_levels
            n_levels = 0
            for j, d in enumerate(levels):
                if d != 0:
                    n_levels = j + 1
            ok = self._tau.set_hierarchical_digits(var.index, levels[:n_levels])
            if not ok:
                raise BatchError(
                    f"SetHierarchicalDigits failed for variable {var.name} "
                    f"levels={levels[:n_levels]} var_len={var.var_len}"
                )

    def _resolve_path(self, filename: str) -> Path:
        """Resolve a file path relative to the data file's directory."""
        p = Path(filename)
        if p.is_file():
            return p
        if self._data_file:
            data_dir = Path(self._data_file).parent
            p2 = data_dir / p.name
            if p2.is_file():
                return p2
        raise BatchError(f"File not found: {filename}")

    def specify_table(self, spec: SpecifyTable) -> None:
        """Register a table specification (safety rules are buffered separately)."""
        self._tables.append(spec)
        self._safety_rules_buf.append([])

    def _finalize_tables(self) -> None:
        """Call native SetTable + SetTableSafety for each registered table."""
        self._n_tables = len(self._tables)
        self._tau.set_number_tab(self._n_tables)
        for i, (spec, srs) in enumerate(self._tables):
            self._call_set_table(i, spec)
            self._call_set_table_safety(i, srs)

    def _call_set_table(self, idx: int, spec: SpecifyTable) -> None:
        if not self._metadata:
            raise BatchError("No metadata.")
        exp_indices = [self._metadata.index_of(n) for n in spec.exp_vars]
        resp_idx = self._metadata.index_of(spec.resp_var)
        resp_var = self._metadata.variables[resp_idx]
        is_freq = resp_var.type == "FREQUENCY"
        # Shadow: native rejects -1 for a non-frequency table, so default to the
        # response variable (Java: indexOfShadowVariable() falls back to resp).
        shadow_idx = -1 if is_freq else (
            self._metadata.index_of(spec.shadow_var) if spec.shadow_var else resp_idx
        )
        # Legacy default: omitted cost means the response variable;
        # explicit special codes -1/-2/-3 map to the native sentinels.
        if not spec.cost_var:
            cost_idx = resp_idx
        elif spec.cost_var == "-1":
            cost_idx = -1
        elif spec.cost_var == "-2":
            cost_idx = -2
        elif spec.cost_var == "-3":
            cost_idx = -3
        else:
            cost_idx = self._metadata.index_of(spec.cost_var)

        req_idx = self._metadata.index_of("REQUEST")

        ok = self._tau.set_table(
            index=idx,
            explanatory_vars=exp_indices,
            is_frequency=is_freq,
            response_var=resp_idx,
            shadow_var=shadow_idx,
            cost_var=cost_idx,
            cellkey_var=self._metadata.index_of("RECORD_KEY"),
            ckm_type=resp_var.ckm_type,
            ckm_topk=resp_var.ckm_top_k,
            lam=spec.lam,
            max_scaled_cost=20000.0,
            peep_var=req_idx,
            missing_as_safe=False,
        )
        if not ok:
            raise BatchError(f"SetTable failed for table {idx + 1}")

    def _call_set_table_safety(self, idx: int, srs: SafetyRuleSet) -> None:
        # Pad freq arrays to at least 2 entries (native expects >= 2)
        min_freq = list(srs.min_freq)
        freq_marge = list(srs.freq_marge)
        while len(min_freq) < 2:
            min_freq.append(0)
        while len(freq_marge) < 2:
            freq_marge.append(0)
        # fix min_freq 0 → 1 (as Java does)
        min_freq = [m if m > 0 else 1 for m in min_freq]
        # if freq_marge[0] < freq_marge[1] and min_freq[1] > 0: freq_marge[0] = freq_marge[1]
        if len(freq_marge) >= 2 and freq_marge[0] < freq_marge[1] and len(min_freq) >= 2 and min_freq[1] > 0:
            freq_marge[0] = freq_marge[1]

        ok = self._tau.set_table_safety(
            index=idx,
            dominance_rule=srs.dom_rule,
            dominance_number=srs.dom_n,
            dominance_k=srs.dom_k,
            pq_rule=srs.pq_rule,
            pq_p=srs.pq_p,
            pq_q=srs.pq_q,
            pq_n=srs.pq_n,
            min_freq=min_freq,
            peep_percentage=srs.peep_percentage,
            peep_marge=srs.peep_marge,
            peep_min_freq=srs.peep_min_freq,
            apply_peep=srs.apply_peep,
            apply_weight=srs.apply_weight,
            weight_on_safety_rule=srs.weight_on_safety,
            apply_holding=srs.apply_holding,
            apply_zero_rule=srs.zero_rule,
            empty_as_non_structural=False,
            ns_empty_safety_range=10,
            zero_safety_range=srs.zero_range,
            manual_safety_perc=srs.manual_perc,
            cell_holding_freq_safety_perc=freq_marge,
        )
        if not ok:
            raise BatchError(f"SetTableSafety failed for table {idx + 1}")

    def read_microdata(self) -> None:
        """Trigger table computation from microdata."""
        self._finalize_tables()
        ok, err, tab = self._tau.compute_tables()
        if not ok:
            raise BatchError(f"ComputeTables failed: {self._tau.error_string(err)} table {tab}")
        logger.info("Computed %d tables.", self._n_tables)

    # -- table-input (tabular data) flow ------------------------------------
    def open_table_data(self, data_file: str, meta: Metadata) -> None:
        """Register a tabular (pre-aggregated) data file + its metadata.

        Like the Java flow, the native engine is only initialised at
        ``<READTABLE>`` time (see :meth:`read_table`); this just records state.
        """
        self._data_file = data_file
        self._metadata = meta
        meta.data_file = data_file
        self._is_table = True

    def _init_table_engine(self, meta: Metadata) -> None:
        """Native setup for the tabular flow (port of ``TableService.readTables``).

        ``CleanAll`` → ``SetInFileInfo(False, sep)`` → ``SetNumberVar`` →
        variable lengths from the data file → ``SetVariable`` → ``ThroughTable``
        → ``SetNumberTab``.
        """
        self._tau.clean_all()
        self._tau.set_in_file_info(False, meta.field_separator)
        self._tau.set_number_var(len(meta.variables))

        self._compute_var_lengths(meta)

        for var in meta.variables:
            self._set_variable(var)

        # Mark that tables are given directly rather than computed from micro
        # data (Java: ThroughTable → m_UsingMicroData = false).
        self._tau.through_table()
        self._tau.set_number_tab(len(self._tables))

    def read_table(self, additivity: int = 0, keep_status: bool = False) -> None:
        """Read a tabular table: port of ``TableSet.read`` (3-phase flow).

        Phase 1: scan the ``.tab`` rows, register categorical codes via
        ``SetInCodeList`` (skipping total/missing rows).
        Then ``SetTotalsInCodeList`` → ``SetTable`` → ``SetTableSafetyInfo``.
        Phase 3: scan again, ``buildCell`` + ``SetInTable`` per row.
        Finally ``CompletedTable`` (optionally computing marginals and
        restoring manually-set statuses when ``keep_status`` is true).
        """
        meta = self._metadata
        if meta is None:
            raise BatchError("No metadata.")
        if not self._is_table:
            raise BatchError("read_table requires open_table_data first.")
        self._init_table_engine(meta)
        sep = meta.field_separator
        path = Path(self._data_file)
        if not path.is_file():
            raise BatchError(f"Table data file not found: {self._data_file}")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        for i, (spec, srs) in enumerate(self._tables):
            exp_idx = [meta.index_of(n) for n in spec.exp_vars]
            if any(j < 0 for j in exp_idx):
                raise BatchError(f"Table {i + 1}: explanatory variable not in metadata.")

            # -- Phase 1: register categorical codes -------------------------
            for line in lines:
                if not line.strip():
                    continue
                fields = self._split_fields(line, sep)
                iexp = 0
                codes: List[str] = []
                skip = False
                for j, var in enumerate(meta.variables):
                    value = fields[j] if j < len(fields) else ""
                    if var.type == "CATEGORICAL":
                        if var.is_total_code(value):
                            skip = True  # total rows are not codes
                            break
                        codes.append(var.normalise_code(value))
                        if var.is_missing(value):
                            skip = True  # missing rows are not codes (Java isMissing)
                            break
                        iexp += 1
                if skip:
                    continue
                if len(codes) != len(exp_idx):
                    continue
                self._set_in_code_list(exp_idx, codes)

            ok, err, _ev = self._tau.set_totals_in_code_list(exp_idx)
            if not ok:
                raise BatchError(
                    f"SetTotalsInCodeList failed for table {i + 1}: "
                    f"{self._tau.error_string(err)}"
                )

            # -- table + safety ---------------------------------------------
            self._call_set_table(i, spec)
            self._call_set_table_safety_info(i, srs)

            # -- Phase 3: fill the cells -------------------------------------
            has_top = meta.contains("TOP_N")
            manual_marge = srs.manual_perc
            for line in lines:
                if not line.strip():
                    continue
                fields = self._split_fields(line, sep)
                cell = self._build_cell(fields, meta, has_top, manual_marge)
                if cell is None:
                    continue
                self._set_in_table(i, cell)

            # -- complete the table ------------------------------------------
            # Legacy: ``computeTotals = (additivity == ADDITIVITY_RECOMPUTE)``.
            # A cover table (``<COVER>``) has additivity NOT_REQUIRED, so its
            # marginals are not recomputed and the additivity check is skipped.
            compute_totals = additivity == _ADDITIVITY_RECOMPUTE
            set_totals_safe = not (srs.freq_marge or srs.dom_rule or srs.pq_rule)
            status_backup = self._backup_cell_statuses(i) if (compute_totals and keep_status) else None
            ok, err = self._tau.completed_table(
                index=i, file="", compute_totals=compute_totals,
                calculated_totals_as_safe=set_totals_safe,
                for_cover_table=self._protect_cover_table,
            )
            if not ok:
                raise BatchError(
                    f"CompletedTable failed for table {i + 1}: {self._tau.error_string(err)}"
                )
            if status_backup is not None:
                self._restore_cell_statuses(i, status_backup)

            self._n_tables = len(self._tables)

    # -- tabular flow helpers ------------------------------------------------
    @staticmethod
    def _split_fields(line: str, sep: str) -> List[str]:
        """Split a data line, unquoting each field (Java Tokenizer.nextField)."""
        out: List[str] = []
        rest = line
        while True:
            idx = rest.find(sep)
            if idx == -1:
                value = rest.strip()
                rest = ""
            else:
                value = rest[:idx].strip()
                rest = rest[idx + 1:].strip()
            if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            out.append(value)
            if rest == "":
                break
        return out

    def _compute_var_lengths(self, meta: Metadata) -> None:
        """Set ``var_len`` from the data file (port of setVariableLengthFromData)."""
        for var in meta.variables:
            var.var_len = 0
        path = Path(self._data_file)
        if not path.is_file():
            return
        sep = meta.field_separator
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            fields = self._split_fields(line, sep)
            for j, var in enumerate(meta.variables):
                if j >= len(fields):
                    break
                if var.is_categorical and var.hierarchical == 2:
                    continue  # HIER_FILE: length comes from the hierarchy file
                if fields[j] == var.tot_code:
                    continue
                if len(fields[j]) > var.var_len:
                    var.var_len = len(fields[j])
        # HIER_FILE variables take their length from the hierarchy file.
        for var in meta.variables:
            if var.is_categorical and var.hierarchical == 2 and var.hier_file_name:
                try:
                    hier = self._resolve_path(var.hier_file_name).read_text(
                        encoding="utf-8", errors="replace")
                except BatchError:
                    continue
                lead = var.leading_string
                for hline in hier.splitlines():
                    hline = hline.strip()
                    while hline.startswith(lead):
                        hline = hline[len(lead):].lstrip()
                    if len(hline) > var.var_len:
                        var.var_len = len(hline)

    def _build_cell(self, fields: List[str], meta: Metadata,
                    has_top: bool, manual_marge: int):
        """Port of ``TableSet.buildCell``. Returns a dict or None to skip.

        ``useStatusOnly`` is True for the batch flow when no (non-MAN) safety
        rule is present, which is exactly when ``m_HasStatus`` is set; the
        status is therefore always kept (Java keeps it whenever the file has
        a status column).
        """
        response = shadow = cost = lower = upper = _CELL_UNKNOWN
        freq = _CELL_UNKNOWN
        status = CS_SAFE  # Java CellStatus.SAFE default
        max_score: List[float] = []
        has_status = False
        codes: List[str] = []

        for j, var in enumerate(meta.variables):
            value = fields[j] if j < len(fields) else ""
            t = var.type
            if t == "CATEGORICAL":
                # isTotalCode2: trimmed equals totCode OR empty → "" (total)
                if value.strip() == "" or value.strip() == var.tot_code:
                    codes.append("")
                else:
                    codes.append(var.normalise_code(value))
            elif t == "RESPONSE":
                if value in ("", "-"):
                    status = CS_EMPTY
                else:
                    response = self._to_double(value)
            elif t == "SHADOW":
                if value not in ("", "-"):
                    shadow = self._to_double(value)
            elif t == "COST":
                if value not in ("", "-"):
                    cost = self._to_double(value)
            elif t == "FREQUENCY":
                if value in ("", "-"):
                    status = CS_EMPTY
                else:
                    freq = int(self._to_double(value))
            elif t == "TOP_N":
                if value in ("", "-"):
                    max_score.append(0.0)
                else:
                    max_score.append(self._to_double(value))
            elif t == "LOWER_PROTECTION_LEVEL":
                if value not in ("", "-"):
                    lower = self._to_double(value)
            elif t == "UPPER_PROTECTION_LEVEL":
                if value not in ("", "-"):
                    upper = self._to_double(value)
            elif t == "STATUS":
                has_status = True
                v = value.upper()
                if v == "":
                    status = CS_EMPTY
                elif v == "E":
                    status = CS_EMPTY
                elif v == meta.safe_status:
                    status = CS_SAFE_MANUAL
                elif v == meta.unsafe_status:
                    status = CS_UNSAFE_MANUAL
                elif v == meta.protect_status:
                    status = CS_PROTECT_MANUAL
                elif v == "M":
                    status = CS_SECONDARY_UNSAFE
                else:
                    raise BatchError(f"Unknown status({value})")

        # Consistency checks (port of buildCell checks 1–7)
        if response == _CELL_UNKNOWN and freq == _CELL_UNKNOWN:
            if status in (CS_SAFE, CS_SAFE_MANUAL):
                status = CS_EMPTY
            if status != CS_EMPTY:
                raise BatchError("An empty cell cannot have a status different from empty")
        if response == _CELL_UNKNOWN and freq != _CELL_UNKNOWN:
            raise BatchError("An empty cell cannot have a real frequency")
        if response not in (_CELL_UNKNOWN, 0) and freq == 0:
            raise BatchError("A real cell cannot have a frequency zero")
        if response != _CELL_UNKNOWN and freq == _CELL_UNKNOWN:
            freq = 1
        if response == _CELL_UNKNOWN and freq == _CELL_UNKNOWN:
            status = CS_EMPTY
        if response == _CELL_UNKNOWN and has_status and status != CS_EMPTY:
            status = CS_EMPTY
        if (response not in (_CELL_UNKNOWN, 0) and freq not in (_CELL_UNKNOWN, 0)
                and status == CS_EMPTY):
            raise BatchError("A non-empty cell cannot have a status empty")

        # Manual-unsafe protection levels
        if status == CS_UNSAFE_MANUAL:
            if lower == _CELL_UNKNOWN:
                lower = abs(response * manual_marge / 100)
                if lower > response:
                    lower = response
            if upper == _CELL_UNKNOWN:
                upper = abs(response * manual_marge / 100)
                if upper > response:
                    upper = response
        else:
            lower = 0
            upper = 0

        if shadow == _CELL_UNKNOWN:
            shadow = response
        if cost == _CELL_UNKNOWN:
            cost = abs(response)
        if cost == 0:
            cost = 0.0001
        if cost < 0:
            raise BatchError("Negative cost value found")

        # TopN must not exceed the total (and must be in non-increasing order).
        if max_score:
            x = sum(max_score)
            if x > response + 1e-9:
                raise BatchError(
                    f"Sum of topN {x} should not exceed the cell total {response}")
            if x > response:
                max_score[0] -= 1e-9
            for j in range(1, len(max_score)):
                if max_score[j] > max_score[j - 1]:
                    raise BatchError(
                        "Error in the order of the TopN.\n"
                        f"{j - 1} = {max_score[j - 1]}\n{j} = {max_score[j]}\n"
                        "This is not allowed.")

        if status == CS_EMPTY or (freq == 0 and response == 0):
            return None  # empty / all-zero cells are not submitted (Java buildCell)

        return {
            "codes": codes, "shadow": shadow, "cost": cost, "response": response,
            "freq": freq, "max_score": max_score, "status": status,
            "lower": lower, "upper": upper,
        }

    @staticmethod
    def _to_double(value: str) -> float:
        """Java StrUtils.toDouble with comma-decimal support."""
        v = value.strip().replace(",", ".")
        try:
            return float(v)
        except ValueError:
            raise BatchError(f'"{value}" is not numeric.')

    def _set_in_code_list(self, exp_idx: List[int], codes: List[str]) -> None:
        """Register codes, retrying with padCode on a hierarchical miss."""
        cur = list(codes)
        for _ in range(len(exp_idx)):
            ok, err, ev = self._tau.set_in_code_list(exp_idx, cur)
            if ok:
                return
            if err != _ERR_CODENOTINCODELIST:
                raise BatchError(
                    f"SetInCodeList failed: {self._tau.error_string(err)} codes={cur}")
            var = self._metadata.variables[exp_idx[ev]]
            cur[ev] = var.pad_code(cur[ev])
        raise BatchError(f"SetInCodeList failed: code not in code list codes={codes}")

    def _set_in_table(self, idx: int, cell: dict) -> None:
        """Fill one cell (codes already normalised by ``_build_cell``)."""
        codes = cell["codes"]
        ok = self._tau.set_in_table(
            index=idx, codes=codes, shadow=cell["shadow"], cost=cell["cost"],
            response=cell["response"], freq=cell["freq"],
            max_score_cell=cell["max_score"], max_score_holding=[],
            status=cell["status"], lpl=cell["lower"], upl=cell["upper"])
        if not ok:
            raise BatchError(
                f"SetInTable failed for table {idx + 1}: codes={codes}")

    def _backup_cell_statuses(self, idx: int):
        ncell, _ = self._tau.get_total_table_size(idx)
        statuses = []
        lpls = []
        upls = []
        for nc in range(ncell):
            statuses.append(self._tau.get_table_cell_status(idx, nc))
            l, u = self._tau.get_table_cell_protection_levels(idx, nc)
            lpls.append(l)
            upls.append(u)
        return statuses, lpls, upls

    def _restore_cell_statuses(self, idx: int, backup) -> None:
        statuses, lpls, upls = backup
        for nc, st in enumerate(statuses):
            if st in (CS_UNKNOWN, CS_EMPTY, 13):  # UNKNOWN/EMPTY/EMPTY_NONSTRUCT
                continue
            self._tau.set_table_cell_status_cell(idx, nc, st)
            self._tau.set_table_cell_protection_levels(idx, nc, lpls[nc], upls[nc])

    def _call_set_table_safety_info(self, idx: int, srs: SafetyRuleSet) -> None:
        """Port of the Java ``SetTableSafetyInfo`` call for a tabular table."""
        has_max_score = self._metadata.contains("TOP_N")
        dom_n = list(srs.dom_n)
        dom_k = list(srs.dom_k)
        while len(dom_n) < 4:
            dom_n.append(0)
            dom_k.append(0)
        # Java passes domN as DominanceNumber and domK as DominancePerc.
        pq_p = list(srs.pq_p)
        pq_q = list(srs.pq_q)
        pq_n = list(srs.pq_n)
        while len(pq_p) < 4:
            pq_p.append(0)
            pq_q.append(0)
            pq_n.append(0)
        has_freq = bool(srs.min_freq)
        freq_perc = srs.freq_marge[0] if srs.freq_marge else 0
        safe_min_rec = srs.min_freq[0] if srs.min_freq else 1
        # useStatusOnly: no (non-MAN) safety rule → status is used directly.
        has_status = not (srs.dom_rule or srs.pq_rule or srs.min_freq
                          or srs.zero_rule or srs.apply_weight or srs.apply_peep)
        ok = self._tau.set_table_safety_info(
            index=idx,
            has_max_score=has_max_score,
            dominance_rule=srs.dom_rule,
            dominance_number=dom_n,
            dominance_perc=dom_k,
            pq_rule=srs.pq_rule,
            pq_p=pq_p, pq_q=pq_q, pq_n=pq_n,
            has_freq=has_freq,
            freq_safety_perc=freq_perc,
            safe_min_rec=safe_min_rec,
            has_status=has_status,
            manual_safety_perc=srs.manual_perc,
            apply_zero_rule=srs.zero_rule,
            zero_safety_range=srs.zero_range,
            empty_as_non_structural=False,
            ns_empty_safety_range=10,
        )
        if not ok:
            raise BatchError(f"SetTableSafetyInfo failed for table {idx + 1}")

    # -- suppress ------------------------------------------------------------
    def suppress(self, cmd: Suppress) -> None:
        """Run suppression on the given table."""
        tab = cmd.tab_no - 1
        kind = cmd.kind.upper()

        if kind == "MOD":
            self._suppress_mod(tab, cmd)
        elif kind == "OPT":
            self._suppress_opt(tab, cmd)
        elif kind == "RND":
            self._suppress_rnd(tab, cmd)
        elif kind == "CKM":
            self._suppress_ckm(tab, cmd)
        else:
            raise BatchError(f"Suppress method '{kind}' not supported in headless mode "
                             f"(GH/NET/CTA require external executables).")

    def _table_cell_bounds(self, tab: int) -> Tuple[float, float]:
        """Return (min, max) finite cell value of ``tab`` (Java minTabVal/maxTabVal)."""
        tau = self._tau
        n, _ = tau.get_total_table_size(tab)
        lo = None
        hi = None
        for i in range(n):
            v = tau.get_table_cell_value(tab, i)
            if v != v:  # NaN
                continue
            if lo is None or v < lo:
                lo = v
            if hi is None or v > hi:
                hi = v
        return (0.0 if lo is None else lo, 0.0 if hi is None else hi)

    def _min_round_base(self, tab: int) -> int:
        """Minimum legal rounding base (Java TableSet.computeMinRoundBase)."""
        mn, mx = self._table_cell_bounds(tab)
        cands = []
        for v in (mn, mx):
            a = abs(v)
            d = 0
            t = float(v)
            while t > 1:
                t /= 10.0
                d += 1
            if d >= 1:
                cands.append(int(10 ** (d - 1)))
        return max(cands) if cands else 0

    def _suppress_mod(self, tab: int, cmd: Suppress) -> None:
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            param_file = os.path.join(tmp, "NPF.txt")
            files_file = os.path.join(tmp, "NFS.txt")
            # PrepareHITAS writes the parameter + file-list for the solver.
            if not self._tau.prepare_hitas(tab, param_file, files_file, tmp):
                raise BatchError("PrepareHITAS failed")
            ok = self._hitas.a_hitas(
                pars_file=param_file,
                files_file=files_file,
                max_time=cmd.mod_max_time,
                single_with_single=cmd.mod_singleton,
                single_with_more=cmd.mod_singleton_multi,
                do_count_bounds=cmd.mod_min_freq,
            )
            if ok > 0:
                raise BatchError(f"AHiTaS failed: {self._hitas.error_string(ok)}")
            ok2, n = self._tau.set_secondary_hitas(tab)
            logger.info("MOD suppress: %d cells set secondary.", n)

    def _suppress_opt(self, tab: int, cmd: Suppress) -> None:
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            jj_in = os.path.join(tmp, "JJ.IN")
            jj_out = os.path.join(tmp, "JJ.OUT")
            jj2 = os.path.join(tmp, "JJ2.OUT")
            lo, hi = self._table_cell_bounds(tab)
            if not self._tau.write_jj_format(tab, jj_in, lo, hi, False, False, False):
                raise BatchError("WriteJJFormat failed for OPT")
            res = self._hitas.full_jj(
                in_file_jj=jj_in, out_file=jj_out, max_time=cmd.opt_max_time
            )
            if res > 1:
                raise BatchError(f"FullJJ failed (code {res}): {self._hitas.error_string(res)}")
            # Append " m" to every line of JJ.OUT (secondary-marker form) -> JJ2.OUT
            with open(jj_out, encoding="utf-8", errors="replace") as fin, \
                 open(jj2, "w", encoding="utf-8") as fout:
                fout.write("fop\n")
                fout.write("fop\n")
                for line in fin:
                    fout.write(line.rstrip("\n") + " m\n")
            code, n = self._tau.set_secondary_jjformat(tab, jj2, False)
            logger.info("OPT suppress: %d cells set secondary.", n)

    def _correct_round_jj(self, path: str, base: int, no_partitions: bool,
                          unit_cost: bool) -> None:
        """Port of ``OptiSuppress.correctRoundJJ`` (pre-rounding fixup of the JJ file).

        For each cell line (``idx resp weight status lb ub lpl upl sliding``):
        - unit-cost: rewrite the weight token to ``1``;
        - unsafe cells whose value is below the rounding base: widen the upper
          protection level so the cell can still be rounded (legacy narrows the
          interval in place; widening to ``base`` is an equivalent safe bound).
        After the cells, if there is a single restriction and no partitions,
        duplicate it (the rounder dislikes a lone restriction).
        """
        with open(path, encoding="utf-8", errors="replace") as fin:
            lines = fin.read().splitlines()
        n = int(lines[1].strip())
        out = [lines[0], lines[1]]
        for i in range(n):
            toks = lines[2 + i].split()
            if len(toks) < 9:
                out.append(lines[2 + i])
                continue
            idx, resp, weight, status, lb, ub, lpl, upl, sliding = toks[:9]
            try:
                resp_v = float(resp)
            except ValueError:
                resp_v = 0.0
            if unit_cost:
                weight = "1"
            if status == "u" and resp_v < base and float(upl) < base:
                upl = f"{base:.6f}"
            out.append(f"{idx} {resp} {weight} {status} {lb} {ub} {lpl} {upl} {sliding}")
        rest = lines[2 + n:]
        if no_partitions and rest and rest[0].strip() == "1":
            rest = ["2"] + [rest[1]] * 2 + rest[2:]
        out.extend(rest)
        with open(path, "w", encoding="utf-8") as fout:
            fout.write("\n".join(out) + "\n")

    def _suppress_rnd(self, tab: int, cmd: Suppress) -> None:
        import tempfile, os
        base = cmd.rnd_base
        min_base = self._min_round_base(tab)
        if base < min_base:
            raise BatchError(f"Rounding base {base} too small; minimum {min_base} required")
        with tempfile.TemporaryDirectory() as tmp:
            jj_in = os.path.join(tmp, "JJ.IN")
            jj_out = os.path.join(tmp, "JJ.OUT")
            jj_stat = os.path.join(tmp, "JJstat.OUT")
            lo, hi = self._table_cell_bounds(tab)
            if not self._tau.write_jj_format(tab, jj_in, lo, hi, False, False, True):
                raise BatchError("WriteJJFormat failed for RND")
            self._correct_round_jj(jj_in, base, cmd.rnd_partitions == 0, cmd.rnd_unit_cost)
            # CRP/HiGHS constants (registry defaults).
            self._rounder.set_double_constant(101, 0.0000001)   # JJZERO
            self._rounder.set_double_constant(102, 21400000000000.0)  # JJINF
            self._rounder.set_double_constant(103, 0.0001)      # JJMINVIOLA
            self._rounder.set_double_constant(104, 0.01)        # JJMAXSLACK
            result, max_jump, n_jump, used, err = self._rounder.do_round(
                solver="SCIP", in_file=jj_in, base=float(base),
                upper_bound=[1.0e40], lower_bound=[0.0],
                solution_file=jj_out, statistics_file=jj_stat,
                max_time=cmd.rnd_time,
            )
            if result > 0:
                raise BatchError(f"DoRound failed (result {result}, err {err})")
            self._tau.set_rounded_response(jj_out, tab)
            self._rounded_tables.add(tab)
            logger.info("RND suppress: table %d rounded (max_jump=%.4f, %d steps).",
                        tab + 1, max_jump, n_jump)

    def _suppress_ckm(self, tab: int, cmd: Suppress) -> None:
        if cmd.ckm_p_table:
            ok, mn, mx = self._tau.set_cell_key_values_freq(tab, cmd.ckm_p_table)
            logger.info("CKM freq: min=%d max=%d", mn, mx)
        elif cmd.ckm_p_table_cont:
            if not self._metadata:
                raise BatchError("No metadata for CKM.")
            resp = self._metadata.variables[0]  # simplified
            self._tau.set_cell_key_values_cont(
                tab, cmd.ckm_p_table_cont, cmd.ckm_p_table_sep,
                resp.ckm_type, resp.ckm_top_k, resp.ckm_zeros, resp.ckm_parity,
                resp.ckm_separation, 0.0, resp.ckm_scaling,
                resp.ckm_sigma0, resp.ckm_sigma1, resp.ckm_xstar, resp.ckm_q,
                resp.ckm_epsilon, cmd.ckm_mu_c,
            )
            logger.info("CKM cont: table %d processed.", tab + 1)
        self._ckm_tables.add(tab)

    # -- audit -----------------------------------------------------------------
    def audit(self, tab: int) -> List[dict]:
        """Compute realized feasibility intervals for table ``tab`` (0-based).

        Port of legacy ``OptiSuppress.RunAudit``: write the table to a JJ file
        (same flags as OPT), run the native audit (the port of
        ``intervalle.exe``; ``dateirechnen=primsec``), and store each
        suppressed cell's realized bounds on the table via
        ``SetRealizedLowerAndUpper`` (readable again through
        ``get_table_cell`` as the final ``rlower``/``rupper`` pair).

        Returns a list of ``{cell, min, max, value, status, unsafe}`` dicts in
        ascending cell-index order (empty when the table has no
        primary/secondary suppressed cells).
        """
        import os
        import tempfile

        lo, hi = self._table_cell_bounds(tab)
        with tempfile.TemporaryDirectory() as tmp:
            jj = os.path.join(tmp, "Anneke.JJ")
            if not self._tau.write_jj_format(tab, jj, lo, hi, False, False, False):
                raise BatchError("WriteJJFormat failed for AUDIT")
            rows = audit_jj(jj)

        by_cell: Dict[int, Tuple[float, float, bool]] = {}
        for r in rows:
            ok = self._tau.set_realized_lower_and_upper(tab, r["cell"],
                                                        r["max"], r["min"])
            if not ok:
                # Legacy raises; the status gate should never reject a
                # suppressed (u/m) cell.
                raise BatchError(
                    f"Audit: could not store realized bounds for cell {r['cell']}")
            by_cell[r["cell"]] = (r["min"], r["max"], bool(r["unsafe"]))
        self._audit[tab] = by_cell
        logger.info("Audit: table %d — %d cells audited.", tab + 1, len(rows))
        return rows

    # -- recode ---------------------------------------------------------------
    def recode(self, cmd: Recode) -> None:
        """Apply a recode to a variable (by name, from the current table context).

        Mirrors ``batch.batchRecode``:
        - a single digit 1..9 → hierarchical truncation to that level;
        - a ``<TREERECODE>`` file → deactivate the listed parent codes;
        - any other file → classic ``DoRecode`` (``dest : src`` spec).
        """
        if not self._metadata:
            raise BatchError("No metadata loaded.")
        var = self._metadata.find(cmd.var_name)
        if var is None:
            raise BatchError(f"Variable '{cmd.var_name}' not found.")
        var_idx = var.index

        recode_src = cmd.recode_file
        # 1) digit truncation level (1..9)
        if len(recode_src) == 1 and "1" <= recode_src <= "9":
            if var.hierarchical == 0:
                raise BatchError(
                    "Truncation is only possible for hierarchical variables")
            self._recode_truncate(var_idx, int(recode_src))
            var.recoded = True
            logger.info("Recode: variable %s truncated to level %s.",
                        var.name, recode_src)
            return

        # 2) file-based recode
        recode_path = self._resolve_path(recode_src)
        first_line = recode_path.read_text(
            encoding="utf-8", errors="replace").splitlines()[0].strip() \
            if recode_path.exists() else ""
        if first_line == "<TREERECODE>":
            self._recode_tree(var_idx, recode_path)
            var.recoded = True
            logger.info("Recode: variable %s tree-recoded (%s).",
                        var.name, recode_src)
            return

        # 3) classic DoRecode (dest : src spec)
        recode_info = _parse_recode_file(recode_path)
        ok, et, el, ep, warn = self._tau.do_recode(
            var_idx, recode_info.data, recode_info.n_missing,
            recode_info.missing1, recode_info.missing2,
        )
        if not ok:
            raise BatchError(f"Recode failed for {var.name}: "
                             f"{self._tau.error_string(et)} line {el} pos {ep} {warn}")
        var.recoded = True
        self._tau.apply_recode()
        logger.info("Recode: variable %s recoded (%s).", var.name, warn or "no warnings")

    def _recode_truncate(self, var_idx: int, max_level: int) -> None:
        """Deactivate every node at ``max_level`` that has children, then recode."""
        self._tau.undo_recode(var_idx)
        n_codes, _ = self._tau.get_var_number_of_codes(var_idx)
        closed = 0
        for i in range(n_codes):
            ok, _, _, _, level, n_child, _ = self._tau.get_var_code_properties(var_idx, i)
            if ok and level == max_level and n_child > 0:
                self._tau.set_var_code_active(var_idx, i, False)
                closed += 1
        if closed > 0:
            if not self._tau.do_active_recode(var_idx):
                self._tau.undo_recode(var_idx)
                raise BatchError("There was a problem recoding this variable")
            self._tau.apply_recode()
        logger.info("Recode: %d nodes closed.", closed)

    def _recode_tree(self, var_idx: int, path: Path) -> None:
        """Deactivate the parent codes listed in a <TREERECODE> file, then recode."""
        self._tau.undo_recode(var_idx)
        # Build code-string -> code-index lookup.
        n_codes, _ = self._tau.get_var_number_of_codes(var_idx)
        index_by_code: Dict[str, int] = {}
        for i in range(n_codes):
            ok, _, cs, _, _ = self._tau.get_var_code(var_idx, i)
            if ok:
                index_by_code[cs] = i
        closed = 0
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            code = line.strip()
            if not code or code == "<TREERECODE>":
                continue
            ci = index_by_code.get(code)
            if ci is None:
                raise BatchError(f"Code ({code}) not found")
            self._tau.set_var_code_active(var_idx, ci, False)
            closed += 1
        if closed > 0:
            if not self._tau.do_active_recode(var_idx):
                self._tau.undo_recode(var_idx)
                raise BatchError("There was a problem recoding this variable")
            self._tau.apply_recode()
        logger.info("Recode: %d tree nodes closed.", closed)

    # -- write ----------------------------------------------------------------
    @staticmethod
    def _top_n_needed(srs: "SafetyRuleSet", holding: bool) -> int:
        """Java ``numberOfTopNNeeded`` / ``numberOfHoldingTopNNeeded``."""
        topn = 0
        lo, hi = (2, 4) if holding else (0, 2)
        for i in range(lo, hi):
            if srs.dom_rule and i < len(srs.dom_n):
                topn = max(topn, srs.dom_n[i])
            if srs.pq_rule and i < len(srs.pq_n) and srs.pq_n[i] != 0:
                topn = max(topn, srs.pq_n[i] + 1)
        return topn

    def write_intermediate_table(self, tab: int, path: str,
                                  simple: bool = False,
                                  holding: bool = False,
                                  suppress_empty: bool = False,
                                  with_audit: bool = False) -> None:
        """Write the legacy WRITETABLE type-5 (INTERMEDIATE) audit file.

        Row-major walk over active codes; port of ``TableSet.write``
        (TableSet.java:1281-1419). With ``with_audit`` (the legacy ``AR+``
        option) six extra columns are appended per row: the realized
        feasibility bounds (``;rlower;rupper;width`` at the table's nDec)
        plus three relative-width columns (``;0;0;0`` for safe/empty/zero
        cells, else two-decimal percentages over the published value),
        matching TableSet.java:1373-1387.
        """
        if not self._metadata:
            raise BatchError("No metadata.")
        if tab < 0 or tab >= len(self._tables):
            raise BatchError(f"Table {tab + 1} out of range.")
        spec, srs = self._tables[tab]
        meta = self._metadata
        tau = self._tau

        exp_names = spec.exp_vars
        exp_vars = [meta.index_of(n) for n in exp_names]
        n_exp = len(exp_vars)

        resp_var = meta.variables[meta.index_of(spec.resp_var)]
        n_dec = resp_var.n_decimals
        is_freq = resp_var.type == "FREQUENCY"
        rounded = tab in self._rounded_tables

        topn = 0 if simple else self._top_n_needed(srs, holding)

        # Per-dim active-code counts (== native SizeDim after recode).
        max_dim = []
        for vi in exp_vars:
            n_codes, n_active = tau.get_var_number_of_codes(vi)
            max_dim.append(n_active)

        # Pre-compute code strings for each (var, code_idx) to avoid repeated
        # native calls inside the inner loop.
        code_strings: List[List[str]] = []
        for vi, md in zip(exp_vars, max_dim):
            var = meta.variables[vi]
            arr: List[str] = []
            for ci in range(md):
                ok, _ctype, cs, _missing, _level = tau.get_var_code(vi, ci)
                if not ok or cs == "":
                    cs = var.tot_code if var.tot_code else "Total"
                arr.append('"' + cs + '"')
            code_strings.append(arr)

        total_cells = 1
        for md in max_dim:
            total_cells *= md

        audit_map = self._audit.get(tab, {}) if with_audit else {}

        with open(path, "w", encoding="utf-8") as f:
            dim_array = [0] * n_exp
            cell_idx = 0
            done = False
            while not done:
                status = 0
                if n_exp == 0:
                    ok_cell = False
                    resp = rresp = cta = ckm = shadow = cost = 0.0
                    key = key_nz = 0.0
                    freq = 0
                    ms_list: List[float] = []
                    msw_list: List[float] = []
                    holding_freq = 0
                    hms_list: List[float] = []
                    hnr_list: List[int] = []
                    peep_cell = peep_holding = 0.0
                    lower = upper = rlower = rupper = 0.0
                else:
                    result = tau.get_table_cell(tab, dim_array, topn)
                    if len(result) < 3:
                        # Out-of-range dim; skip (should not happen)
                        resp = rresp = cta = ckm = shadow = cost = 0.0
                        key = key_nz = 0.0
                        freq = 0
                        status = CS_EMPTY
                        ms_list = [0.0] * topn
                        msw_list = [0.0] * topn
                        holding_freq = 0
                        hms_list = [0.0] * topn
                        hnr_list = [0] * topn
                        peep_cell = peep_holding = 0.0
                        lower = upper = rlower = rupper = 0.0
                    else:
                        (ok_cell, resp, rresp, cta, ckm, shadow, cost,
                         key, key_nz, freq, status,
                         ms_list, msw_list,
                         holding_freq, hms_list, hnr_list,
                         peep_cell, peep_holding,
                         lower, upper, rlower, rupper) = result

                skip = (status == CS_EMPTY and suppress_empty)
                if not skip:
                    parts: List[str] = []
                    for j in range(n_exp):
                        parts.append(code_strings[j][dim_array[j]])
                    rv = rresp if rounded else resp
                    parts.append(f"{rv:.{n_dec}f}")
                    if not is_freq:
                        parts.append(str(holding_freq if holding else freq))
                        if not simple:
                            parts.append(f"{shadow:.{n_dec}f}")
                    parts.append(f"{cost:.{n_dec}f}")
                    if not simple:
                        if holding:
                            for j in range(topn):
                                v = hms_list[j] if j < len(hms_list) else 0.0
                                parts.append(f"{v:.{n_dec}f}")
                        else:
                            for j in range(topn):
                                v = ms_list[j] if j < len(ms_list) else 0.0
                                parts.append(f"{v:.{n_dec}f}")
                    parts.append(_status_symbol(status))
                    parts.append(f"{lower:.{n_dec}f}")
                    parts.append(f"{upper:.{n_dec}f}")
                    if with_audit:
                        if cell_idx in audit_map:
                            rlo, rup, _unsaf = audit_map[cell_idx]
                        else:
                            rlo = rup = 0.0
                        parts.append(f"{rlo:.{n_dec}f}")
                        parts.append(f"{rup:.{n_dec}f}")
                        parts.append(f"{rup - rlo:.{n_dec}f}")
                        # Legacy: response==0 or safe-not-protected
                        # (CellStatus category SAFE_NOT_PROTECTED =
                        # {1, 2, 13}) -> zeros; else percentages.
                        if rv == 0 or status in (CS_SAFE, CS_SAFE_MANUAL,
                                                 CS_EMPTY_NONSTRUCT):
                            parts.extend(["0", "0", "0"])
                        else:
                            parts.append(f"{100 * (rv - rlo) / rv:.2f}")
                            parts.append(f"{100 * (rup - rv) / rv:.2f}")
                            parts.append(f"{100 * (rup - rlo) / rv:.2f}")
                    f.write(";".join(parts) + "\n")

                cell_idx += 1
                # advance dim_array (odometer, rightmost = last var)
                k = n_exp - 1
                while k >= 0:
                    dim_array[k] += 1
                    if dim_array[k] < max_dim[k]:
                        break
                    else:
                        dim_array[k] = 0
                        k -= 1
                if k == -1:
                    done = True

    def write_table(self, cmd: WriteTable) -> None:
        """Write a table to file."""
        tab = cmd.tab_no - 1
        fpath = self._work_dir / cmd.file if cmd.file else self._work_dir / f"table_{tab + 1}.tab"

        # Response type follows the legacy SaveTable: 0 = raw values,
        # 1 = rounded (after RND), 3 = cell-key protected. (The legacy
        # batch processor derives it from tableSet.rounded/ckmProtect;
        # writing the rounded response before RND would yield all zeros.)
        if tab in self._ckm_tables:
            resp = 3
        elif tab in self._rounded_tables:
            resp = 1
        else:
            resp = 0

        if cmd.output_type == 1:  # CSV
            self._tau.write_csv(tab, str(fpath), True, IDENTITY_DIM_SEQUENCE, resp)
        elif cmd.output_type == 5:  # INTERMEDIATE (audit table)
            opts = set(cmd.options)
            simple = "+SO" in opts
            holding = "+HI" in opts
            suppress_empty = "+SE" in opts
            # AR+: audit the table first (legacy SaveTable runs the
            # intervalle audit right before writing when the flag is set).
            with_audit = "+AR" in opts
            if with_audit:
                self.audit(tab)
            self.write_intermediate_table(tab, str(fpath),
                                          simple=simple,
                                          holding=holding,
                                          suppress_empty=suppress_empty,
                                          with_audit=with_audit)
        elif cmd.output_type in (2, 3, 4, 6, 7):
            # CellRecords (SBS, code value, etc.)
            self._tau.write_cell_records(tab, str(fpath), True, False,
                                         False, "", False, True, resp)
        else:
            raise BatchError(f"Unknown output type {cmd.output_type}")
        logger.info("Wrote table %d to %s", tab + 1, fpath)

    # -- apriori --------------------------------------------------------------
    def _bogus_range(self, vi: int, idx: int,
                     n_active: int,
                     levels: List[Tuple[int, int]]) -> List[int]:
        """Java ``CodeList.bogusRange(activeCodeIndex)`` — a code plus the run
        of single-child ancestors below it and single-child descendants above
        it. ``levels[i] = (n_children, level)`` over all *active* codes."""
        lo = idx
        while lo > 0:
            pn, pl = levels[lo - 1]
            cn, cl = levels[lo]
            if pn == 1 and pl == cl - 1:
                lo -= 1
            else:
                break
        hi = idx
        while hi + 1 < n_active:
            pn, pl = levels[hi]
            cn, cl = levels[hi + 1]
            if pn == 1 and cl == pl + 1:
                hi += 1
            else:
                break
        return list(range(lo, hi + 1))

    def apply_apriori(self, cmd: "Apriory") -> Dict[str, int]:
        """Apply an apriori file to a computed table.

        Port of Java ``APriori.processAprioryFile`` (APriori.java:701). Each
        line names a cell by its code values and a change type:

        - ``S``/``U``/``P``/``M``/``ML`` -> set the cell status
        - ``C``/``W`` <cost>             -> set the cell cost
        - ``PL`` <lpl> <upl>             -> set the protection levels
          (only for primary-unsafe cells, 3..9)
        - ``AB`` <l> <u>                 -> not implemented (legacy either)

        With ``expand_bogus`` the change is applied to the whole bogus range
        (single-child chain) of each named code, matching the legacy
        ``expandBogus`` flag. Returns a count summary dict.
        """
        from .batch import Apriory  # local to avoid a top-level cycle

        if not self._metadata:
            raise BatchError("No metadata.")
        tab = cmd.tab_no - 1
        if tab < 0 or tab >= len(self._tables):
            raise BatchError(f"Table {cmd.tab_no} out of range.")
        spec, _srs = self._tables[tab]
        meta = self._metadata
        tau = self._tau
        sep = cmd.separator

        exp_vars = [meta.index_of(n) for n in spec.exp_vars]
        n_exp = len(exp_vars)
        if n_exp == 0:
            raise BatchError("Table has no exposure variables for apriori.")

        # Per-exp-var: active code strings, (n_children, level) of active
        # codes, and a code-string -> active-index lookup. Java trims both the
        # input code and the codelist entry before comparing; for
        # non-hierarchical vars it left-pads to ``var_len``. We index on both
        # the raw and trimmed/padded forms so any of them resolves.
        code_str: List[List[str]] = []
        levels: List[List[Tuple[int, int]]] = []
        code_index: List[Dict[str, int]] = []
        max_dim: List[int] = []
        for vi in exp_vars:
            n_codes, n_active = tau.get_var_number_of_codes(vi)
            max_dim.append(n_active)
            var = meta.variables[vi]
            arr: List[str] = []
            lv: List[Tuple[int, int]] = []
            idx: Dict[str, int] = {}
            for ci in range(n_active):
                ok2, _ctype, cs, _m2, _l2 = tau.get_var_code(vi, ci)
                if not ok2:
                    continue
                cs = cs or ""
                # Level/n_children only for the bogus range; GetVarCodeProperties
                # can fail on non-hierarchical vars, so fall back to (0, 0).
                pok, _p, _a, _m, lev, nch, _pcs = \
                    tau.get_var_code_properties(vi, ci)
                if not pok:
                    lev, nch = 0, 0
                arr.append(cs)
                lv.append((nch, lev))
                pos = len(arr) - 1
                # Index on raw, trimmed, and (non-hierarchical) padded forms.
                for cand in (cs, cs.strip(), var.normalise_code(cs.strip())):
                    if cand and cand not in idx:
                        idx[cand] = pos
            code_str.append(arr)
            levels.append(lv)
            code_index.append(idx)

        # Counters mirroring Java aPrioryStatus[5][2] (row, [ok, error]).
        #   0 line read, 1 status, 2 cost, 3 bounds, 4 protection level
        stats: Dict[str, int] = {
            "lines_read": 0, "lines_error": 0,
            "status_ok": 0, "status_err": 0,
            "cost_ok": 0, "cost_err": 0,
            "bounds_ok": 0, "bounds_err": 0,
            "protlevel_ok": 0, "protlevel_err": 0,
        }

        apriory_status_map = {
            "S": CS_SAFE_MANUAL, "U": CS_UNSAFE_MANUAL,
            "P": CS_PROTECT_MANUAL, "M": CS_SECONDARY_UNSAFE_MANUAL,
            "ML": CS_SECONDARY_UNSAFE,
        }

        apri_path = self._resolve_arb_path(cmd.file)
        with open(apri_path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()

        first_line = True
        for line in lines:
            if not line.strip():
                continue
            parts = line.split(sep)

            # --- first line: validate separator + field count ---------------
            # Java ``NumberOfVarsInAprioryFile``: the apriori row is
            # ``<code1>;...;<codeN>;<cmd> [;<v1> [;<v2>]]``. The *command*
            # token is the last non-numeric field, so the number of code
            # fields is (n_fields - 1). The first line is validated but also
            # applied as a normal data row (there is no separate header).
            if first_line:
                if sep not in line:
                    raise BatchError(
                        f"separator ({sep!r}) not found in file {apri_path}")
                n_fields = len(parts) - 1  # last field is the command
                try:
                    float(parts[-1])
                    n_fields -= 1  # a numeric tail means a value, not a code
                except ValueError:
                    pass
                if n_fields != n_exp:
                    raise BatchError(
                        f"Apriori file contains {n_fields} fields, but the "
                        f"table has {n_exp}.")
                first_line = False

            # --- part 1: read the codes -> dimIndex -------------------------
            # Java: a total/blank code maps to the parent (dim 0); otherwise
            # the (trimmed) code is matched against the active codelist.
            base_dim: List[int] = []
            ok = True
            for i in range(n_exp):
                var = meta.variables[exp_vars[i]]
                if i >= len(parts):
                    ok = False
                    base_dim.append(0)
                    continue
                raw = parts[i].strip()
                if raw == "" or var.is_total_code(raw):
                    base_dim.append(0)
                    continue
                ci = None
                for cand in (raw, var.normalise_code(raw)):
                    if cand in code_index[i]:
                        ci = code_index[i][cand]
                        break
                if ci is None:
                    stats["lines_error"] += 1
                    if not cmd.ignore_error:
                        raise BatchError(
                            f"Apriori code {i + 1} '{raw}' not found in "
                            f"variable {var.name}.")
                    ok = False
                    base_dim.append(0)
                else:
                    base_dim.append(ci)
            if ok:
                stats["lines_read"] += 1
            if not ok:
                continue  # legacy: skip this line

            # --- part 2: the apriory change ---------------------------------
            if n_exp >= len(parts):
                # no change-type token present
                stats["lines_error"] += 1
                continue
            ap_type = parts[n_exp].strip().upper()
            rest = parts[n_exp + 1:]
            x1 = x2 = 0.0
            new_status = 0

            if ap_type in apriory_status_map:
                new_status = apriory_status_map[ap_type]
            elif ap_type in ("C", "W"):
                new_status = _AP_ADJUST_COST
                if rest:
                    x1 = float(rest[0])
            elif ap_type == "AB":
                raise BatchError(
                    "Apriori bounds (AB) are not implemented (nor in legacy).")
            elif ap_type == "PL":
                new_status = _AP_ADJUST_PROT_LEVEL
                if len(rest) >= 2:
                    x1, x2 = float(rest[0]), float(rest[1])
            else:
                stats["lines_error"] += 1
                if not cmd.ignore_error:
                    raise BatchError(
                        f"Illegal apriori command {ap_type!r} in file "
                        f"{apri_path}.")
                continue

            # --- expand to bogus ranges and apply over the Cartesian product
            ranges: List[List[int]] = []
            for i in range(n_exp):
                if cmd.expand_bogus and levels[i]:
                    ranges.append(self._bogus_range(
                        exp_vars[i], base_dim[i], max_dim[i], levels[i]))
                else:
                    ranges.append([base_dim[i]])

            for dim in itertools.product(*ranges):
                self._apply_apriori_change(
                    tab, dim, ap_type, x1, x2, new_status, tau, stats)

        logger.info(
            "Apriori: table %d — %d lines read, %d status, %d cost, "
            "%d prot-level changes applied.",
            cmd.tab_no, stats["lines_read"], stats["status_ok"],
            stats["cost_ok"], stats["protlevel_ok"])
        return stats

    def _apply_apriori_change(self, tab: int, dim: Tuple[int, ...],
                               ap_type: str, x1: float, x2: float,
                               new_status: int, tau,
                               stats: Dict[str, int]) -> None:
        """Apply a single apriori change to one cell (APriori.ProcessOneVarCode)."""
        try:
            res = tau.get_table_cell(tab, list(dim), 0)
        except Exception:
            return
        if not res or not res[0]:
            return
        old_status = res[10]
        if old_status in (CS_EMPTY, CS_EMPTY_NONSTRUCT):
            return  # "met status EMPTY mag niks gebeuren"

        if ap_type in ("C", "W"):  # change cost
            cost = x1 if x1 > 0 else 1.0
            if tau.set_table_cell_cost(tab, list(dim), cost):
                stats["cost_ok"] += 1
            else:
                stats["cost_err"] += 1
            return

        if ap_type == _AP_ADJUST_PROT_LEVEL or ap_type == "PL":
            if x1 < 0 or x2 < 0:
                stats["protlevel_err"] += 1
                return
            if not (CS_UNSAFE_RULE <= old_status <= CS_UNSAFE_MANUAL):
                stats["protlevel_err"] += 1
                return
            # Apply to the primary-unsafe cell's protection levels (dim-based).
            ok = tau.set_protection_levels_dim(tab, list(dim), x1, x2)
            stats["protlevel_ok" if ok else "protlevel_err"] += 1
            return

        if ap_type in ("S", "U", "P", "M", "ML"):  # status change
            # PROTECTED -> UNSAFE is disallowed by the native setter; also
            # mirror the M/ML -> SECONDARY_UNSAFE_MANUAL mapping from Java.
            if (new_status == CS_SECONDARY_UNSAFE
                    and old_status == CS_SAFE_MANUAL):
                new_status = CS_SECONDARY_UNSAFE_MANUAL
            if tau.set_table_cell_status_dim(tab, list(dim), new_status):
                stats["status_ok"] += 1
            else:
                stats["status_err"] += 1
            return

    # -- batch execution ------------------------------------------------------
    def run_batch(self, arb_path: Union[str, Path]) -> None:
        """Parse and execute a legacy .arb batch file."""
        arb_path = Path(arb_path)
        # Resolve file paths relative to the .arb file's directory
        self._work_dir = arb_path.parent
        commands = parse_batch(arb_path)

        # Reset state
        self._tables = []
        self._safety_rules_buf = []
        self._rounded_tables = set()
        self._ckm_tables = set()
        self._protect_cover_table = False
        self._audit = {}

        for cmd in commands:
            self._execute(cmd)
        return self

    def _execute(self, cmd: Command) -> None:
        if isinstance(cmd, OpenMicrodata):
            self._data_file = self._resolve_arb_path(cmd.file)
            logger.info("Open microdata: %s", cmd.file)
        elif isinstance(cmd, OpenTableData):
            self._data_file = self._resolve_arb_path(cmd.file)
            self._is_table = True
            logger.info("Open table data: %s", cmd.file)
        elif isinstance(cmd, OpenMetadata):
            meta_path = self._resolve_arb_path(cmd.file)
            self._metadata = parse_rda_table(meta_path) if self._is_table else parse_rda(meta_path)
            logger.info("Loaded metadata: %d variables.", len(self._metadata.variables))
            # Set variable indices
            for i, v in enumerate(self._metadata.variables):
                v.index = i
        elif isinstance(cmd, SpecifyTable):
            self._tables.append((cmd, SafetyRuleSet()))
            self._safety_rules_buf.append([])
            logger.info("Table %d: %s | %s", len(self._tables),
                        cmd.exp_vars, cmd.resp_var)
        elif isinstance(cmd, SafetyRule):
            if self._safety_rules_buf:
                self._safety_rules_buf[-1].append(cmd)
        elif isinstance(cmd, ReadMicrodata):
            self._apply_safety_rules()
            self.open_microdata(self._data_file, self._metadata)
            self.read_microdata()
        elif isinstance(cmd, ReadTable):
            self._apply_safety_rules()
            self.read_table(cmd.additivity, cmd.keep_status)
        elif isinstance(cmd, Recode):
            self.recode(cmd)
        elif isinstance(cmd, Suppress):
            self.suppress(cmd)
        elif isinstance(cmd, WriteTable):
            self.write_table(cmd)
        elif isinstance(cmd, (GoInteractive, Logbook, VersionInfo, Anco)):
            logger.info("Skipping %s (no-op in headless mode).", type(cmd).__name__)
        elif isinstance(cmd, Clear):
            self._tables = []
            self._safety_rules_buf = []
            self._metadata = None
            self._rounded_tables = set()
            self._ckm_tables = set()
            self._protect_cover_table = False
            self._audit = {}
            logger.info("Cleared state.")
        elif isinstance(cmd, Solver):
            if cmd.name not in ("HIGHS", "FREE"):
                logger.warning(
                    "Solver %s is not supported; using HiGHS instead (license ignored).",
                    cmd.name,
                )
        elif isinstance(cmd, Apriory):
            self.apply_apriori(cmd)
        elif isinstance(cmd, Cover):
            # Legacy (batch.java:281): global protect-cover-table flag plus
            # table 0's additivity set to NOT_REQUIRED. In the legacy flow
            # ``addAdditivityParamBatch`` resets it on the following
            # ``<READTABLE>``, so the global flag is what drives the solve:
            # ``CompletedTable(ForCoverTable=true)`` skips the additivity
            # check and marginals are not recomputed. The GUI's heavy
            # LinkedTables machinery (generating cover tables via
            # intervalle.exe) is out of scope for the headless CLI.
            self._protect_cover_table = True
            logger.info("COVER: protect-cover-table enabled (additivity check skipped).")

    def _resolve_arb_path(self, filename: str) -> str:
        """Resolve a file path relative to the .arb file's directory."""
        p = Path(filename)
        if p.is_file():
            return str(p)
        resolved = self._work_dir / filename
        if resolved.is_file():
            return str(resolved)
        return filename  # let the caller raise

    def _apply_safety_rules(self) -> None:
        """Aggregate buffered safety rules into each table's SafetyRuleSet."""
        if self._safety_rules_buf:
            for i, rules in enumerate(self._safety_rules_buf):
                if self._tables and i < len(self._tables):
                    self._tables[i] = (self._tables[i][0], SafetyRuleSet.from_rules(rules))


# ===========================================================================
# Recode file parser
# ===========================================================================
@dataclass
class RecodeInfo:
    data: str
    missing1: str
    missing2: str
    code_list: str
    n_missing: int = 0


def _parse_recode_file(path: Union[str, Path]) -> RecodeInfo:
    """Parse a .grc recode file (port of Variable.readRecodeFile)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    tok = Tokenizer(text)
    recode_data = ""
    missing1 = ""
    missing2 = ""
    code_list = ""

    while tok.next_line() is not None:
        hs = tok.get_line()
        token = tok.next_token()
        if token == "<MISSING>":
            missing1 = tok.next_token()
            if missing1 == "":
                raise BatchError("No missing values found after <MISSING>")
            token = tok.next_token()
            if token == ",":
                token = tok.next_token()
            missing2 = token
        elif token == "<CODELIST>":
            code_list = tok.next_token()
        elif token != "":
            recode_data = recode_data + hs + "\n"

    n_missing = 0
    if missing1:
        n_missing = 1
    if missing2:
        n_missing = 2

    return RecodeInfo(data=recode_data.replace("\n", "\r\n"),
                      missing1=missing1, missing2=missing2,
                      code_list=code_list, n_missing=n_missing)


# ===========================================================================
# Convenience: one-shot batch run
# ===========================================================================
def run_batch(arb_path: Union[str, Path]) -> "Engine":
    """Parse and execute a .arb batch file in a fresh Engine.

    Returns the configured ``Engine`` (with tables computed) for inspection.
    """
    return Engine().run_batch(arb_path)

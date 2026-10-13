"""Legacy-compatible Tau-Argus ``.arb`` batch file parser.

Faithful Python port of the grammar in ``src/tauargus/model/batch.java`` and
``src/tauargus/utils/Tokenizer.java``. The goal is drop-in compatibility: a
``.arb`` file written for the legacy Java front-end parses with the same
keywords, argument forms, and status-machine ordering rules.

This module is a *parser only*. It tokenises and validates a batch file and
emits a list of command objects (see the ``*Command`` dataclasses). Execution
of those commands against the native engine lives in ``engine.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

# ---------------------------------------------------------------------------
# Status machine (mirrors batch.java's `status` int)
#   0 = start
#   1 = data / table file found
#   2 = meta file found
#   3 = specify-tables found
#   4 = safety rule found
# ---------------------------------------------------------------------------
STATUS_START = 0
STATUS_DATA = 1
STATUS_META = 2
STATUS_SPECIFY = 3
STATUS_SAFETY = 4


class BatchError(Exception):
    """Raised on a malformed batch file or an out-of-order command."""


# ===========================================================================
# Tokenizer — 1:1 port of argus.utils.Tokenizer
# ===========================================================================
class Tokenizer:
    def __init__(self, text: str):
        self._lines = text.splitlines()
        self._pos = 0
        self.line = ""
        self.line_number = 0

    # -- raw line reading ----------------------------------------------------
    def next_line(self) -> Optional[str]:
        """Read the next non-empty, non-comment line (or None at EOF).

        Mirrors batch.java's loop: blank lines and lines starting with ``//``
        are skipped. Tabs are normalised to spaces and the line is stripped.
        """
        while self._pos < len(self._lines):
            raw = self._lines[self._pos].replace("\t", " ").strip()
            self._pos += 1
            self.line_number += 1
            if raw == "" or raw.startswith("//"):
                continue
            self.line = raw
            return self.line
        self.line = ""
        return None

    def close(self) -> None:
        self._lines = []

    # -- token reading -------------------------------------------------------
    def next_token(self) -> str:
        line = self.line
        if line.startswith("//"):
            self.line = ""
            return ""
        if line.startswith('"'):
            begin = 1
            end = line.find('"', 1)
            new_begin = end + 1
        elif line[:1] in (",", "|", "(", ")"):
            begin = 0
            end = 1
            new_begin = 1
        else:
            begin = 0
            end = line.find(" ")
            new_begin = end + 1

        if end != -1:
            value = line[begin:end]
            self.line = line[new_begin:].strip()
        else:
            value = line[begin:]
            self.line = ""
        if value.startswith("<"):
            value = value.upper()
        return value

    def next_field(self, separator: str) -> str:
        idx = self.line.find(separator)
        if idx == -1:
            value = self.line.strip()
            self.line = ""
        else:
            value = self.line[:idx].strip()
            self.line = self.line[idx + 1:].strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        return value

    def next_char(self) -> str:
        if len(self.line) == 1:
            value = self.line
            self.line = ""
        else:
            value = self.line[:1]
            self.line = self.line[1:].strip()
        return value

    def test_next_char(self) -> str:
        return self.line[:1] if self.line != "" else ""

    def get_line(self) -> str:
        return self.line

    def clear_line(self) -> None:
        self.line = ""


# ===========================================================================
# tail-string helpers (mirror batch.java's `String[] tail` + nextToken/nextChar)
# ===========================================================================
_SEPARATORS = ("(", ",", ")", "|")


def _tail_next_token(tail: List[str]) -> str:
    """Return the token before the first ( , ) | separator in tail[0]."""
    s = tail[0]
    if s == "":
        return ""
    positions = [s.find(ch) for ch in _SEPARATORS]
    pmin = min((p for p in positions if p >= 0), default=100000)
    hs = s[:pmin].strip()
    tail[0] = s[pmin:].strip()
    if pmin == 100000:
        raise BatchError(f"No separator found in string {tail[0]}")
    if len(hs) >= 2 and hs.startswith('"') and hs.endswith('"'):
        hs = hs[1:-1]
    return hs


def _tail_next_char(tail: List[str]) -> str:
    s = tail[0]
    if s == "":
        return ""
    hs = s[:1]
    tail[0] = s[1:]
    return hs


def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s


# ===========================================================================
# Command dataclasses
# ===========================================================================
@dataclass
class OpenMicrodata:
    file: str


@dataclass
class OpenTableData:
    file: str


@dataclass
class OpenMetadata:
    file: str


@dataclass
class SpecifyTable:
    exp_vars: List[str]
    resp_var: str
    shadow_var: str = ""
    cost_var: str = ""          # "" | "-1" | "-2" | "-3" | var name
    lam: float = 0.0


@dataclass
class SafetyRule:
    """One rule tuple, e.g. P(25,100,1), FREQ(3,30), WGT(1), ..."""
    kind: str
    args: List[str] = field(default_factory=list)


@dataclass
class ReadMicrodata:
    pass


@dataclass
class ReadTable:
    additivity: int = 0
    keep_status: bool = False


@dataclass
class Apriory:
    file: str
    tab_no: int
    separator: str
    ignore_error: bool = True
    expand_bogus: bool = False


@dataclass
class Cover:
    pass


@dataclass
class Suppress:
    kind: str                   # GH | MOD | OPT | NET | RND | CTA | CKM
    tab_no: int
    params: List[str] = field(default_factory=list)  # raw, post-tabno params
    # normalised, filled by the specific _parse_* helpers:
    gh_apriory_pct: int = 0
    gh_size: int = 0
    gh_singleton: bool = False
    mod_max_time: int = 0
    mod_singleton: bool = False
    mod_singleton_multi: bool = False
    mod_min_freq: bool = False
    mod_msc: float = 0.0
    mod_lower_marg: float = 0.0
    mod_upper_marg: float = 0.0
    opt_max_time: int = 0
    rnd_base: int = 0
    rnd_step: int = 0
    rnd_time: int = 10
    rnd_partitions: int = 0
    rnd_stop_rule: int = 2
    rnd_unit_cost: bool = True
    ckm_p_table: str = ""
    ckm_p_table_cont: str = ""
    ckm_p_table_sep: str = ""
    ckm_mu_c: float = 0.0


@dataclass
class WriteTable:
    tab_no: int
    output_type: int            # 1..7 (CSV, CSV pivot, code value, SBS, ...)
    options: List[str] = field(default_factory=list)  # e.g. +AS, -SE, +FL
    file: str = ""


@dataclass
class Recode:
    tab_no: int
    var_name: str
    recode_file: str            # filename or a digit 1..9 (truncation level)


@dataclass
class Solver:
    name: str                   # XPRESS | CPLEX | FREE
    license_file: str = ""


@dataclass
class GoInteractive:
    pass


@dataclass
class Logbook:
    path: str


@dataclass
class VersionInfo:
    path: str


@dataclass
class Anco:
    pass


@dataclass
class Clear:
    pass


Command = Union[
    OpenMicrodata, OpenTableData, OpenMetadata, SpecifyTable, SafetyRule,
    ReadMicrodata, ReadTable, Apriory, Cover, Suppress, WriteTable, Recode,
    Solver, GoInteractive, Logbook, VersionInfo, Anco, Clear,
]


# ===========================================================================
# Command argument parsers (mirror batch.java's per-case logic)
# ===========================================================================
def _parse_specify_table(tok: Tokenizer) -> SpecifyTable:
    exp_vars: List[str] = []
    while True:
        name = tok.next_token()  # handles a leading quoted name internally
        exp_vars.append(name)
        c = tok.test_next_char()
        if c not in ("|", '"'):
            raise BatchError(f"A '|' or a '\"' is expected here (got '{c}')")
        if c == "|":
            break
        # c == '"' -> next iteration reads the following quoted var name

    tok.next_char()  # consume '|'
    resp = tok.next_field("|")

    shadow = ""
    cost = ""
    lam = 0.0
    if tok.get_line() != "":
        shadow = tok.next_field("|")
        if tok.get_line() != "":
            cost = tok.next_field(",")
        rest = tok.get_line()
        if rest != "":
            lam = float(rest)

    return SpecifyTable(exp_vars=exp_vars, resp_var=resp,
                        shadow_var=shadow, cost_var=cost, lam=lam)


def _parse_safety_rule(tok: Tokenizer) -> List[SafetyRule]:
    rules: List[SafetyRule] = []
    rule_type = tok.next_field("(").upper()
    if rule_type == "":
        return rules  # "<SAFETYRULE>" with no rule -> use given status

    # Re-assemble the remaining "(....|....)" remainder.
    tail = ["(" + tok.get_line()]
    tok.clear_line()

    while rule_type != "":
        c = _tail_next_char(tail)
        if c != "(":
            raise BatchError("A ( is expected here")
        token = _tail_next_token(tail)
        args: List[str] = []
        args.append(token)
        # consume comma-separated args until the closing ')'
        while True:
            c = _tail_next_char(tail)
            if c == ",":
                args.append(_tail_next_token(tail))
                continue
            if c == ")":
                break
            if c == "":
                break
            raise BatchError(f"A ',' or ')' is expected here (got '{c}')")
        rules.append(SafetyRule(kind=rule_type, args=args))

        c = _tail_next_char(tail)  # expect '|' or ''
        if c not in ("|", ""):
            raise BatchError(f"A '|' is expected here (got '{c}')")
        if c == "":
            break
        rule_type = _tail_next_token(tail).upper()

    return rules


def _parse_suppress(tok: Tokenizer) -> Suppress:
    kind = tok.next_field("(").upper()
    tail = [tok.get_line()]
    tok.clear_line()

    token = _tail_next_token(tail)
    tab_no = int(token)
    cmd = Suppress(kind=kind, tab_no=tab_no)

    if kind == "GH":
        c = _tail_next_char(tail)
        if c == ",":
            cmd.gh_apriory_pct = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        if c == ",":
            cmd.gh_size = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        if c == ",":
            cmd.gh_singleton = _tail_next_token(tail) == "1"
            c = _tail_next_char(tail)
    elif kind == "MOD":
        c = _tail_next_char(tail)
        if c == ",":
            cmd.mod_max_time = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        i = 0
        while c == "," and i < 3:
            i += 1
            val = _tail_next_token(tail)
            if i == 1:
                cmd.mod_singleton = val == "1"
            elif i == 2:
                cmd.mod_singleton_multi = val == "1"
            elif i == 3:
                cmd.mod_min_freq = val == "1"
            c = _tail_next_char(tail)
        if c == ",":  # MSC, LOWERMARG, UPPERMARG
            msc = _tail_next_token(tail)
            if msc != "":
                cmd.mod_msc = float(msc)
            c = _tail_next_char(tail)
            if c == ",":
                lm = _tail_next_token(tail)
                if lm != "":
                    cmd.mod_lower_marg = float(lm)
                c = _tail_next_char(tail)
            if c == ",":
                um = _tail_next_token(tail)
                if um != "":
                    cmd.mod_upper_marg = float(um)
    elif kind == "OPT":
        c = _tail_next_char(tail)
        if c == ",":
            cmd.opt_max_time = int(_tail_next_token(tail))
    elif kind == "RND":
        c = _tail_next_char(tail)
        if c != ",":
            raise BatchError("a comma(,) expected after RND base")
        cmd.rnd_base = int(_tail_next_token(tail))
        c = _tail_next_char(tail)
        if c == ",":  # steps
            cmd.rnd_step = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        if c == ",":  # max time
            cmd.rnd_time = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        if c == ",":  # partitions
            cmd.rnd_partitions = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        if c == ",":  # stop rule
            cmd.rnd_stop_rule = int(_tail_next_token(tail))
            c = _tail_next_char(tail)
        if c == ",":  # unit cost
            cmd.rnd_unit_cost = _tail_next_token(tail) == "1"
    elif kind == "CKM":
        c = _tail_next_char(tail)
        if c != ")":
            raise BatchError("A ) is expected here for CKM")
        rem = tail[0].strip()
        if rem == "":
            return cmd
        # magnitude tables use | separators; freq tables a single filename
        if "|" in rem:
            parts = rem.split("|")
            p1 = parts[0].strip()
            if p1 and not p1.startswith("//"):
                cmd.ckm_p_table_cont = _unquote(p1)
            if len(parts) > 1 and parts[1].strip() and not parts[1].strip().startswith("//"):
                cmd.ckm_p_table_sep = _unquote(parts[1].strip())
            if len(parts) > 2 and parts[2].strip():
                cmd.ckm_mu_c = float(parts[2].strip())
        else:
            hs = _unquote(rem)
            if not hs.startswith("//"):
                cmd.ckm_p_table = hs

    cmd.params = [tail[0].strip()] if tail[0].strip() else []
    return cmd


def _parse_write_table(tok: Tokenizer) -> WriteTable:
    tail = [tok.get_line()]
    tok.clear_line()
    c = _tail_next_char(tail)
    if c != "(":
        raise BatchError(f"( expected; not a {c}")
    tab_no = int(_tail_next_token(tail))
    c = _tail_next_char(tail)
    if c != ",":
        raise BatchError(f", expected; not a {c}")
    output_type = int(_tail_next_token(tail))
    if not (1 <= output_type <= 7):
        raise BatchError(f"Unknown output type ({output_type})")
    c = _tail_next_char(tail)
    if c != ",":
        raise BatchError(f", expected; not a {c}")

    options: List[str] = []
    while True:
        hs = _tail_next_token(tail).upper()
        if hs == "":
            break
        if len(hs) < 3:
            raise BatchError(f"Unknown option ({hs})")
        opt = hs[:2]
        rest = hs[2:]
        sign = rest[:1]
        if sign not in ("+", "-"):
            raise BatchError(f"+ or - expected; not a {sign}")
        options.append(sign + opt)

    c = _tail_next_char(tail)
    if c != ",":
        raise BatchError(f", expected; not a {c}")
    file_name = _tail_next_token(tail)
    return WriteTable(tab_no=tab_no, output_type=output_type,
                     options=options, file=file_name)


def _parse_recode(tok: Tokenizer) -> Recode:
    tail = [tok.get_line()]
    tok.clear_line()
    token = _tail_next_token(tail)
    tab_no = int(token)
    c = _tail_next_char(tail)
    if c != ",":
        raise BatchError(f"Illegal character {c} found. Comma expected")
    var_name = _tail_next_token(tail)
    c = _tail_next_char(tail)
    if c != ",":
        raise BatchError(f"Illegal character {c} found. comma expected")
    recode_file = _unquote(tail[0].strip())
    return Recode(tab_no=tab_no, var_name=var_name, recode_file=recode_file)


def _parse_apriory(tok: Tokenizer) -> Apriory:
    f_name = tok.next_token()
    c = tok.next_char()
    if c != ",":
        raise BatchError("a , was expected here")
    hs = tok.next_field(",")
    tab_no = int(hs)
    if not tok.test_next_char() == '"':
        raise BatchError("A quote was expected here before the separator")
    tok.next_char()  # consume opening quote
    # The separator is a quoted string (legacy form: ``","`` / ``";"``);
    # read it up to the closing quote.
    idx = tok.line.find('"')
    if idx == -1:
        sep = tok.line.strip()
        tok.line = ""
    else:
        sep = tok.line[:idx].strip()
        tok.line = tok.line[idx + 1:].strip()
    if sep == "":
        raise BatchError("No separator was specified")
    ignore_error = True
    expand_bogus = False
    c = tok.next_char()
    if c != "":
        if c != ",":
            raise BatchError("A comma was expected here")
        hs = tok.next_field(",")
        if hs != "":
            if hs == "1":
                ignore_error = True
            elif hs == "0":
                ignore_error = False
            else:
                raise BatchError(f"Illegal field ({hs}) for ignore error")
        hs = tok.next_field(",")
        if hs != "":
            expand_bogus = (hs == "1")
    return Apriory(file=f_name, tab_no=tab_no, separator=sep,
                   ignore_error=ignore_error, expand_bogus=expand_bogus)


def _parse_read_table(tok: Tokenizer) -> ReadTable:
    tail = [tok.next_token()]
    c = _tail_next_char(tail)
    if c == "":
        c = "0"
    if c not in ("0", "1", "2"):
        raise BatchError(f"Illegal parameter ({c}) for ReadTable")
    additivity = int(c)
    keep_status = False
    if tail[0] != "" and additivity == 1:
        c = _tail_next_char(tail)
        if c in ("T", "t"):
            keep_status = True
        elif c not in ("F", "f"):
            raise BatchError(f"Invalid option {c}")
    return ReadTable(additivity=additivity, keep_status=keep_status)


# ===========================================================================
# Top-level parser
# ===========================================================================
def parse_batch(path: Union[str, Path]) -> List[Command]:
    """Parse a legacy ``.arb`` batch file into a list of command objects.

    Raises :class:`BatchError` on malformed syntax or an out-of-order command
    (the same positions the legacy front-end rejected).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    tok = Tokenizer(text)
    commands: List[Command] = []
    status = STATUS_START
    data_file = ""

    while tok.next_line() is not None:
        token = tok.next_token()
        if token.startswith("\\\\") or token.startswith("//"):
            continue  # comment

        if token == "<OPENMICRODATA>":
            if status != STATUS_START:
                raise BatchError(f"{token} is not allowed in this position")
            data_file = tok.next_token()
            status = STATUS_DATA
            commands.append(OpenMicrodata(file=data_file))
        elif token == "<OPENTABLEDATA>":
            if status not in (STATUS_START, STATUS_SAFETY):
                raise BatchError(f"{token} is not allowed in this position")
            data_file = tok.next_token()
            status = STATUS_DATA
            commands.append(OpenTableData(file=data_file))
        elif token == "<OPENMETADATA>":
            if data_file == "":
                raise BatchError("A data file must be specified first")
            if status != STATUS_DATA:
                raise BatchError(f"{token} is not allowed in this position")
            meta = tok.next_token()
            status = STATUS_META
            data_file = ""
            commands.append(OpenMetadata(file=meta))
        elif token == "<SPECIFYTABLE>":
            if status not in (STATUS_META, STATUS_SAFETY):
                raise BatchError(f"{token} is not allowed in this position")
            commands.append(_parse_specify_table(tok))
            status = STATUS_SPECIFY
        elif token == "<SAFETYRULE>":
            if status != STATUS_SPECIFY:
                raise BatchError(f"{token} is not allowed in this position")
            rules = _parse_safety_rule(tok)
            status = STATUS_SAFETY
            commands.extend(rules)
        elif token in ("<READMICRODATA>",):
            if status != STATUS_SAFETY:
                raise BatchError(f"{token} is not allowed in this position")
            commands.append(ReadMicrodata())
        elif token == "<READTABLE>":
            if status == STATUS_SPECIFY:
                status = STATUS_SAFETY
            if status != STATUS_SAFETY:
                raise BatchError(f"{token} is not allowed in this position")
            commands.append(_parse_read_table(tok))
        elif token in ("<APRIORY>", "<APRIORI>"):
            commands.append(_parse_apriory(tok))
        elif token == "<COVER>":
            commands.append(Cover())
        elif token == "<SUPPRESS>":
            if status != STATUS_SAFETY:
                raise BatchError(f"{token} is not allowed in this position")
            commands.append(_parse_suppress(tok))
        elif token == "<WRITETABLE>":
            commands.append(_parse_write_table(tok))
        elif token == "<RECODE>":
            commands.append(_parse_recode(tok))
        elif token == "<SOLVER>":
            name = tok.next_field(",").upper()
            lic = tok.next_field(",")
            commands.append(Solver(name=name, license_file=_unquote(lic)))
        elif token == "<GOINTERACTIVE>":
            commands.append(GoInteractive())
        elif token == "<LOGBOOK>":
            commands.append(Logbook(path=_unquote(tok.get_line())))
            tok.clear_line()
        elif token == "<VERSIONINFO>":
            commands.append(VersionInfo(path=_unquote(tok.get_line())))
            tok.clear_line()
        elif token == "<ANCO>":
            commands.append(Anco())
        elif token == "<JJ>":
            continue  # not implemented in legacy either; skipped
        elif token == "<CLEAR>":
            commands.append(Clear())
            status = STATUS_START
        elif token == "<COMMENT>":
            continue
        else:
            raise BatchError(f"Unknown keyword {token}")

    tok.close()
    return commands

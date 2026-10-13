"""Tests for the legacy ``.rda`` metadata parsers.

Pins the parsed variable structure (type, position/length, missing values,
hierarchy, codelists, decimals) for both the microdata and tabular sample
metadata files, matching ``Metadata.readMicroMetadata`` /
``Metadata.readTableMetadata``.
"""

from pathlib import Path

from pytauargus.engine import parse_rda, parse_rda_table

from conftest import DATA


class TestMicroMetadata:
    @classmethod
    def setup_class(cls):
        cls.meta = parse_rda(DATA / "tau_testW.rda")

    def test_variable_count(self):
        assert len(self.meta.variables) == 14

    def test_names_and_types(self):
        got = [(v.name, v.type) for v in self.meta.variables]
        assert got == [
            ("Year", "CATEGORICAL"),
            ("IndustryCode", "CATEGORICAL"),
            ("Size", "CATEGORICAL"),
            ("Region", "CATEGORICAL"),
            ("Wgt", "WEIGHT"),
            ("Var1", "RESPONSE"),
            ("Var2", "RESPONSE"),
            ("Var3", "RESPONSE"),
            ("Var4", "RESPONSE"),
            ("Var5", "RESPONSE"),
            ("Var6", "RESPONSE"),
            ("Var7", "RESPONSE"),
            ("Var8", "RESPONSE"),
            ("Request", "REQUEST"),
        ]

    def test_indexes_sequential(self):
        assert [v.index for v in self.meta.variables] == list(range(14))

    def test_fixed_width_layout(self):
        by = {v.name: v for v in self.meta.variables}
        assert (by["Year"].b_pos, by["Year"].var_len) == (1, 2)
        assert (by["Size"].b_pos, by["Size"].var_len) == (9, 2)
        assert (by["Region"].b_pos, by["Region"].var_len) == (12, 2)
        assert (by["Wgt"].b_pos, by["Wgt"].var_len) == (15, 4)
        assert (by["Var2"].b_pos, by["Var2"].var_len) == (28, 10)

    def test_missing_values(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Year"].missing[0] == "99"
        assert by["Year"].missing[1] == ""
        assert by["Region"].missing[0] == "99"
        assert by["Wgt"].missing[0] == "9999"

    def test_hierarchy_levels(self):
        by = {v.name: v for v in self.meta.variables}
        # IndustryCode: <HIERLEVELS> 3 1 1 0 0
        assert by["IndustryCode"].hierarchical == 1
        assert by["IndustryCode"].hier_levels[:3] == [3, 1, 1]
        assert by["IndustryCode"].hier_levels_sum == 5

    def test_hierarchy_file(self):
        by = {v.name: v for v in self.meta.variables}
        # Region: <HIERCODELIST> "region2.hrc" <HIERLEADSTRING> "@"
        assert by["Region"].hierarchical == 2
        assert by["Region"].hier_file_name == "region2.hrc"
        assert by["Region"].leading_string == "@"

    def test_codelist(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Region"].code_list_file == "region.cdl"

    def test_decimals(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Var2"].n_decimals == 2
        assert by["Wgt"].n_decimals == 1
        assert by["Year"].n_decimals == 0

    def test_distance_function(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Year"].has_distance_function
        assert by["Year"].distance_function == [1, 2, 3, 4, 5]

    def test_request_codes(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Request"].request_code == ["1", "2"]

    def test_type_helpers(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Year"].is_categorical
        assert by["Var2"].is_response
        assert by["Var2"].is_numeric
        assert by["Wgt"].is_weight
        assert by["Year"].n_missings() == 1
        assert by["Var2"].get_total_code() == "Total"  # empty -> default


class TestTableMetadata:
    @classmethod
    def setup_class(cls):
        cls.meta = parse_rda_table(DATA / "tableinput" / "pp.rda")

    def test_is_table(self):
        assert self.meta.is_table is True

    def test_separator(self):
        assert self.meta.field_separator == ";"

    def test_variable_count(self):
        assert len(self.meta.variables) == 11

    def test_types(self):
        got = [(v.name, v.type) for v in self.meta.variables]
        assert got == [
            ("Size", "CATEGORICAL"),
            ("Region", "CATEGORICAL"),
            ("Var2", "RESPONSE"),
            ("FreqVar", "FREQUENCY"),
            ("Var2_Shadow", "SHADOW"),
            ("Var2_Cost", "COST"),
            ("Top1", "TOP_N"),
            ("Top2", "TOP_N"),
            ("StatusVar", "STATUS"),
            ("LowerProtLevel", "LOWER_PROTECTION_LEVEL"),
            ("UpperProtLevel", "UPPER_PROTECTION_LEVEL"),
        ]

    def test_no_fixed_width(self):
        # Tabular declarations carry no bPos/varLen (both stay at defaults).
        for v in self.meta.variables:
            assert v.b_pos == 1
            assert v.var_len == 0

    def test_totcode(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Size"].tot_code == "Total"
        assert by["Region"].tot_code == "Total"
        assert by["Var2"].tot_code == ""

    def test_hierarchy_file(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Region"].hierarchical == 2
        assert by["Region"].hier_file_name == "region2.hrc"
        assert by["Region"].leading_string == "@"
        assert by["Region"].code_list_file == "REGION.CDL"

    def test_decimals(self):
        by = {v.name: v for v in self.meta.variables}
        assert by["Var2"].n_decimals == 2

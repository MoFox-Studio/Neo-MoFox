"""测试 Schema 生成工具函数。"""

from __future__ import annotations

from typing import Annotated


from src.core.components.utils.schema_utils import (
    extract_description_from_docstring,
    map_type_to_json,
    parse_function_signature,
)


class TestMapTypeToJson:
    """测试类型映射到 JSON Schema 类型。"""

    def test_map_int(self):
        """测试映射 int 类型。"""
        assert map_type_to_json(int) == "integer"

    def test_map_float(self):
        """测试映射 float 类型。"""
        assert map_type_to_json(float) == "number"

    def test_map_str(self):
        """测试映射 str 类型。"""
        assert map_type_to_json(str) == "string"

    def test_map_bool(self):
        """测试映射 bool 类型。"""
        assert map_type_to_json(bool) == "boolean"

    def test_map_list(self):
        """测试映射 list 类型。"""
        assert map_type_to_json(list) == "array"

    def test_map_dict(self):
        """测试映射 dict 类型。"""
        assert map_type_to_json(dict) == "object"

    def test_map_none_type(self):
        """测试映射 None 类型。"""
        assert map_type_to_json(type(None)) == "null"

    def test_map_optional_int(self):
        """测试映射 Optional[int]。"""
        from typing import Optional

        result = map_type_to_json(Optional[int])
        assert result == "integer"

    def test_map_optional_str(self):
        """测试映射 Optional[str]。"""
        from typing import Optional

        result = map_type_to_json(Optional[str])
        assert result == "string"

    def test_map_union_with_none(self):
        """测试映射 Union[int, None]。"""
        from typing import Union

        result = map_type_to_json(Union[int, None])
        assert result == "integer"

    def test_map_list_of_ints(self):
        """测试映射 list[int]。"""
        result = map_type_to_json(list[int])
        assert result == "array"

    def test_map_dict_str_int(self):
        """测试映射 dict[str, int]。"""
        result = map_type_to_json(dict[str, int])
        assert result == "object"

    def test_map_annotated_int(self):
        """测试映射 Annotated[int, 'description']。"""
        result = map_type_to_json(Annotated[int, "这是一个整数参数"])
        assert result == "integer"

    def test_map_annotated_str(self):
        """测试映射 Annotated[str, 'description']。"""
        result = map_type_to_json(Annotated[str, "文本内容"])
        assert result == "string"

    def test_map_string_type_hint_int(self):
        """测试映射字符串类型注解 'int'。"""
        assert map_type_to_json("int") == "integer"

    def test_map_string_type_hint_float(self):
        """测试映射字符串类型注解 'float'。"""
        assert map_type_to_json("float") == "number"

    def test_map_string_type_hint_str(self):
        """测试映射字符串类型注解 'str'。"""
        assert map_type_to_json("str") == "string"

    def test_map_string_type_hint_bool(self):
        """测试映射字符串类型注解 'bool'。"""
        assert map_type_to_json("bool") == "boolean"

    def test_map_string_type_hint_list(self):
        """测试映射字符串类型注解 'list'。"""
        assert map_type_to_json("list") == "array"

    def test_map_string_type_hint_dict(self):
        """测试映射字符串类型注解 'dict'。"""
        assert map_type_to_json("dict") == "object"

    def test_map_string_type_hint_none(self):
        """测试映射字符串类型注解 'none'。"""
        result = map_type_to_json("none")
        assert result == "null"

    def test_map_string_type_hint_nonetype(self):
        """测试映射字符串类型注解 'nonetype'。"""
        result = map_type_to_json("nonetype")
        assert result == "null"

    def test_map_pep604_optional(self):
        """测试映射 PEP 604 可空联合类型。"""
        result = map_type_to_json(list[str] | None)
        assert result == "array"

    def test_map_string_type_hint_unknown(self):
        """测试映射未知字符串类型注解。"""
        assert map_type_to_json("unknown") == "string"

    def test_map_unknown_type(self):
        """测试映射未知类型。"""
        class CustomType:
            pass

        assert map_type_to_json(CustomType) == "string"

    def test_map_tuple(self):
        """测试映射 tuple 类型。"""
        # tuple 不在 _TYPE_MAPPING 中，应该返回默认值 "string"
        result = map_type_to_json(tuple)
        # 由于 get_origin(tuple) 返回 tuple，不在容器类型列表中
        # 所以会返回 "string"（默认值）
        assert result == "string" or result == "object"

    def test_map_set(self):
        """测试映射 set 类型。"""
        # set 在容器类型检查中，应该返回 "object"
        result = map_type_to_json(set)
        # 由于 set 在容器类型列表中，会被映射到 "object"
        assert result == "object" or result == "string"


class TestParseFunctionSignature:
    """测试函数签名解析。"""

    def test_parse_simple_function(self):
        """测试解析简单函数。"""

        def example_func(arg1: int, arg2: str):
            """示例函数。

            Args:
                arg1: 第一个参数
                arg2: 第二个参数
            """
            pass

        schema = parse_function_signature(
            example_func,
            "example_func",
            "示例函数",
        )

        assert schema["type"] == "function"
        assert schema["function"]["name"] == "example_func"
        assert schema["function"]["description"] == "示例函数"
        assert "arg1" in schema["function"]["parameters"]["properties"]
        assert "arg2" in schema["function"]["parameters"]["properties"]

    def test_parse_function_with_defaults(self):
        """测试解析带默认值的函数。"""

        def func_with_default(a: int, b: str = "default"):
            """带默认值的函数。

            Args:
                a: 必需参数
                b: 可选参数
            """
            pass

        schema = parse_function_signature(func_with_default, "func", "描述")

        assert schema["function"]["parameters"]["required"] == ["a"]
        assert "b" in schema["function"]["parameters"]["properties"]
        assert schema["function"]["parameters"]["properties"]["b"]["default"] == "default"

    def test_parse_function_skip_none_default(self):
        """测试默认值为 None 时不写入 default。"""

        def func_with_none_default(a: list[str] | None = None):
            """带 None 默认值。"""
            pass

        schema = parse_function_signature(func_with_none_default, "func", "描述")

        a_prop = schema["function"]["parameters"]["properties"]["a"]
        assert a_prop["type"] == "array"
        assert a_prop["items"]["type"] == "string"
        assert "default" not in a_prop

    def test_parse_function_array_has_items(self):
        """测试数组参数会生成 items 子 schema。"""

        def func_with_array_tags(tags: list[str]):
            """数组参数函数。"""
            pass

        schema = parse_function_signature(func_with_array_tags, "func", "描述")
        tags_prop = schema["function"]["parameters"]["properties"]["tags"]
        assert tags_prop["type"] == "array"
        assert tags_prop["items"]["type"] == "string"

    def test_parse_function_dict_has_additional_properties(self):
        """测试字典参数会生成 additionalProperties。"""

        def func_with_dict_meta(meta: dict[str, int]):
            """字典参数函数。"""
            pass

        schema = parse_function_signature(func_with_dict_meta, "func", "描述")
        meta_prop = schema["function"]["parameters"]["properties"]["meta"]
        assert meta_prop["type"] == "object"
        assert meta_prop["additionalProperties"]["type"] == "integer"

    def test_parse_function_skip_self(self):
        """测试解析时跳过 self 参数。"""

        class TestClass:
            def method(self, arg1: int, arg2: str):
                """类方法。

                Args:
                    arg1: 参数1
                    arg2: 参数2
                """
                pass

        schema = parse_function_signature(TestClass.method, "method", "方法")

        assert "self" not in schema["function"]["parameters"]["properties"]
        assert "arg1" in schema["function"]["parameters"]["properties"]

    def test_parse_function_skip_args_kwargs(self):
        """测试解析时跳过 *args 和 **kwargs。"""

        def func_with_varargs(a: int, *args: str, **kwargs: int):
            """带可变参数的函数。

            Args:
                a: 必需参数
            """
            pass

        schema = parse_function_signature(func_with_varargs, "func", "描述")

        assert "a" in schema["function"]["parameters"]["properties"]
        assert "args" not in schema["function"]["parameters"]["properties"]
        assert "kwargs" not in schema["function"]["parameters"]["properties"]

    def test_parse_function_with_annotated(self):
        """测试解析带 Annotated 的函数。"""

        def func_with_annotated(
            name: Annotated[str, "用户名"],
            age: Annotated[int, "用户年龄"],
        ):
            """带 Annotated 的函数。"""
            pass

        schema = parse_function_signature(func_with_annotated, "func", "描述")

        assert schema["function"]["parameters"]["properties"]["name"]["description"] == "用户名"
        assert schema["function"]["parameters"]["properties"]["age"]["description"] == "用户年龄"

    def test_parse_function_with_mixed_annotations(self):
        """测试解析混合注解的函数。"""

        def mixed_func(
            name: Annotated[str, "名称"],
            age: int,
            active: bool = True,
        ):
            """混合注解函数。

            Args:
                name: 名称参数
                age: 年龄参数
                active: 活跃状态
            """
            pass

        schema = parse_function_signature(mixed_func, "mixed", "混合")

        props = schema["function"]["parameters"]["properties"]
        assert props["name"]["description"] == "名称"
        assert props["age"]["description"] == "年龄参数"
        assert props["active"]["description"] == "活跃状态"

    def test_parse_function_without_docstring(self):
        """测试解析没有 docstring 的函数。"""

        def no_docstring(arg1: int, arg2: str):
            pass

        schema = parse_function_signature(no_docstring, "func", "描述")

        # 应该使用默认描述
        props = schema["function"]["parameters"]["properties"]
        assert "arg1 参数" in props["arg1"]["description"]
        assert "arg2 参数" in props["arg2"]["description"]

    def test_parse_function_with_empty_args_section(self):
        """测试解析空 Args 段的函数。"""

        def empty_args(a: int, b: str):
            """函数描述。

            没有 Args 段。
            """
            pass

        schema = parse_function_signature(empty_args, "func", "描述")

        # 应该使用默认描述
        props = schema["function"]["parameters"]["properties"]
        assert "a 参数" in props["a"]["description"]

    def test_parse_function_multiline_arg_description(self):
        """测试解析多行参数描述的函数。"""

        def multiline_desc(
            param1: int,
        ):
            """多行描述函数。

            Args:
                param1: 这是第一行
                    这是第二行
                    这是第三行
            """
            pass

        schema = parse_function_signature(multiline_desc, "func", "描述")

        desc = schema["function"]["parameters"]["properties"]["param1"]["description"]
        # 应该合并多行
        assert "第一行" in desc
        assert "第二行" in desc

    def test_parse_function_with_complex_types(self):
        """测试解析复杂类型的函数。"""

        def complex_func(
            items: list[str],
            mapping: dict[str, int],
            flag: bool,
        ):
            """复杂类型函数。

            Args:
                items: 列表参数
                mapping: 字典参数
                flag: 布尔参数
            """
            pass

        schema = parse_function_signature(complex_func, "complex", "复杂")

        props = schema["function"]["parameters"]["properties"]
        assert props["items"]["type"] == "array"
        assert props["mapping"]["type"] == "object"
        assert props["flag"]["type"] == "boolean"


class TestExtractDescriptionFromDocstring:
    """测试从 docstring 提取描述。"""

    def test_extract_from_simple_docstring(self):
        """测试从简单 docstring 提取描述。"""

        def simple_func():
            """这是一个简单的函数。"""
            pass

        desc = extract_description_from_docstring(simple_func)
        assert desc == "这是一个简单的函数。"

    def test_extract_from_multiline_docstring(self):
        """测试从多行 docstring 提取描述。"""

        def multiline_func():
            """这是第一行描述。

            更多详细信息...
            """
            pass

        desc = extract_description_from_docstring(multiline_func)
        assert desc == "这是第一行描述。"

    def test_extract_from_empty_docstring(self):
        """测试从空 docstring 提取描述。"""

        def empty_doc_func():
            """"""
            pass

        desc = extract_description_from_docstring(empty_doc_func)
        assert desc == ""

    def test_extract_from_no_docstring(self):
        """测试从没有 docstring 的函数提取描述。"""

        def no_doc_func():
            pass

        desc = extract_description_from_docstring(no_doc_func)
        assert desc == ""

    def test_extract_from_docstring_with_args(self):
        """测试从带 Args 段的 docstring 提取描述。"""

        def with_args_func(arg1: int):
            """函数描述。

            Args:
                arg1: 参数描述
            """
            pass

        desc = extract_description_from_docstring(with_args_func)
        assert desc == "函数描述。"


class TestSchemaUtilsEdgeCases:
    """测试 Schema 工具的边界情况。"""

    def test_parse_function_with_positional_only_args(self):
        """测试解析仅位置参数的函数。"""

        def pos_only_func(a: int, /, b: str):
            """仅位置参数函数。

            Args:
                a: 仅位置参数
                b: 普通参数
            """
            pass

        schema = parse_function_signature(pos_only_func, "func", "描述")

        # 应该包含两个参数
        assert "a" in schema["function"]["parameters"]["properties"]
        assert "b" in schema["function"]["parameters"]["properties"]

    def test_parse_function_with_keyword_only_args(self):
        """测试解析仅关键字参数的函数。"""

        def kw_only_func(*, a: int, b: str):
            """仅关键字参数函数。

            Args:
                a: 仅关键字参数
                b: 仅关键字参数
            """
            pass

        schema = parse_function_signature(kw_only_func, "func", "描述")

        assert "a" in schema["function"]["parameters"]["properties"]
        assert "b" in schema["function"]["parameters"]["properties"]

    def test_parse_function_all_optional(self):
        """测试解析所有参数都是可选的函数。"""

        def all_optional_func(a: int = 1, b: str = "default"):
            """所有参数可选的函数。"""
            pass

        schema = parse_function_signature(all_optional_func, "func", "描述")

        # required 列表应该为空
        assert schema["function"]["parameters"]["required"] == []

    def test_map_union_multiple_types(self):
        """测试映射 Union 多个类型。"""
        from typing import Union

        # Union[str, int, float] 应该映射到第一个非 None 类型
        result = map_type_to_json(Union[str, int, float])
        assert result == "string"

    def test_parse_with_return_annotation(self):
        """测试解析带返回类型注解的函数。"""

        def with_return(a: int) -> str:
            """带返回类型的函数。

            Args:
                a: 输入参数
            """
            return str(a)

        schema = parse_function_signature(with_return, "func", "描述")

        # 返回类型不应该在 schema 中
        assert "return" not in schema["function"]["parameters"]

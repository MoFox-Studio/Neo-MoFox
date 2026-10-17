"""测试 src.core.utils.user_query_helper 模块。"""

from unittest.mock import AsyncMock, MagicMock, patch


from src.core.utils.user_query_helper import UserQueryHelper, get_user_query_helper


class TestUserQueryHelper:
    """测试 UserQueryHelper 类。"""

    def test_initialization(self):
        """测试初始化。"""
        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            assert helper is not None

    def test_generate_raw_person_id(self):
        """测试生成原始 person_id。"""
        helper = UserQueryHelper()
        result = helper.generate_raw_person_id("telegram", "user123")
        assert result == "telegram:user123"

    def test_generate_person_id(self):
        """测试生成 person_id（哈希）。"""
        helper = UserQueryHelper()
        id1 = helper.generate_person_id("telegram", "user123")
        id2 = helper.generate_person_id("telegram", "user123")
        id3 = helper.generate_person_id("telegram", "user456")

        # 相同的输入应该生成相同的 ID
        assert id1 == id2
        # 不同的输入应该生成不同的 ID
        assert id1 != id3
        # ID 应该是 64 字符的 SHA256 哈希
        assert len(id1) == 64

    def test_generate_person_id_cache(self):
        """测试 person_id 生成缓存。"""
        helper = UserQueryHelper()

        # 第一次调用会计算哈希
        id1 = helper.generate_person_id("telegram", "user123")
        # 第二次调用应该从缓存获取
        id2 = helper.generate_person_id("telegram", "user123")

        assert id1 == id2
        # 验证缓存工作（info 会显示缓存命中）
        assert helper.generate_person_id.cache_info().hits > 0

    def test_get_or_create_person_existing(self):
        """测试获取或创建用户（已存在）。"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        # 模拟已存在的用户
        mock_person = MagicMock()
        mock_person.id = 1
        mock_person.interaction_count = 5

        # 使用 patch.object 来 patch UserQueryHelper 的 __init__ 方法
        # 在初始化后直接设置 mock 实例
        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            # 直接设置 helper 的 person_crud 属性的 async 方法
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            person, is_new = asyncio.run(helper.get_or_create_person("telegram", "user123"))

            assert person == mock_person
            assert is_new is False
            helper.person_crud.update.assert_called_once()

    def test_get_or_create_person_new(self):
        """测试获取或创建用户（新用户）。"""
        import asyncio

        # 使用 patch.object 来 mock CRUDBase
        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            # 直接设置 helper 的 person_crud 属性的 async 方法
            helper.person_crud.get_by = AsyncMock(return_value=None)
            helper.person_crud.create = AsyncMock(return_value=MagicMock(id=1))

            person, is_new = asyncio.run(
                helper.get_or_create_person("telegram", "user123", nickname="TestUser")
            )

            assert is_new is True
            helper.person_crud.create.assert_called_once()

    def test_get_or_create_person_updates_last_interaction_each_call(self):
        """每次调用 get_or_create_person 都应更新 last_interaction（不被缓存）。"""
        import asyncio
        import time

        mock_person = MagicMock()
        mock_person.id = 1
        mock_person.interaction_count = 5

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            # 连续调用两次（相同参数），都应该触发数据库 update
            asyncio.run(helper.get_or_create_person("telegram", "user123"))
            # 稍作停顿以确保时间戳不同
            time.sleep(0.01)
            asyncio.run(helper.get_or_create_person("telegram", "user123"))

            # update 应被调用两次（缓存装饰器移除后不再跳过写操作）
            assert helper.person_crud.update.call_count == 2

            # 第二次 update 的 last_interaction 应大于等于第一次
            # 调用签名：update(id, obj_in_dict)
            first_obj_in = helper.person_crud.update.call_args_list[0].args[1]
            second_obj_in = helper.person_crud.update.call_args_list[1].args[1]
            assert (
                second_obj_in["last_interaction"]
                >= first_obj_in["last_interaction"]
            )
            assert second_obj_in["interaction_count"] == 6

    def test_update_person_info_updates_last_interaction_and_count(self):
        """update_person_info 应更新 last_interaction 和 interaction_count（消息接收流程实际调用此方法）。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 42
        mock_person.interaction_count = 7
        # 显式置空名称相关字段，避免 MagicMock 默认值干扰改名检测
        mock_person.nickname = None
        mock_person.cardname = None
        mock_person.nickname_history = None

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            result = asyncio.run(
                helper.update_person_info(
                    "telegram", "user123", nickname="Nick", cardname="Card"
                )
            )

            assert result is True
            helper.person_crud.update.assert_called_once()
            # 调用签名：update(id, obj_in_dict)
            obj_in = helper.person_crud.update.call_args.args[1]
            # 必须包含 last_interaction 和 interaction_count 字段
            assert "last_interaction" in obj_in
            assert "interaction_count" in obj_in
            assert obj_in["interaction_count"] == 8
            assert obj_in["nickname"] == "Nick"
            assert obj_in["cardname"] == "Card"
            # 旧名为空，不应写入历史
            assert "nickname_history" not in obj_in

    def test_update_person_info_not_blocked_by_cache(self):
        """同一用户连续调用 update_person_info 不应被缓存跳过（写操作必须每次执行）。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 42
        mock_person.interaction_count = 0
        mock_person.nickname = None
        mock_person.cardname = None
        mock_person.nickname_history = None

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            # 连续三次相同参数调用，都应该都触发 update
            for _ in range(3):
                asyncio.run(
                    helper.update_person_info(
                        "telegram", "user123", nickname="Nick", cardname="Card"
                    )
                )

            assert helper.person_crud.update.call_count == 3

    def test_update_person_info_records_nickname_change_in_history(self):
        """nickname 变更时，旧名应进入 nickname_history，新名替换当前。"""
        import asyncio
        import json

        mock_person = MagicMock()
        mock_person.id = 100
        mock_person.interaction_count = 3
        mock_person.nickname = "OldNick"
        mock_person.cardname = None
        mock_person.nickname_history = None

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            asyncio.run(
                helper.update_person_info(
                    "telegram", "user123", nickname="NewNick"
                )
            )

            helper.person_crud.update.assert_called_once()
            obj_in = helper.person_crud.update.call_args.args[1]
            assert obj_in["nickname"] == "NewNick"
            # 旧名应被推入历史
            assert "nickname_history" in obj_in
            history = json.loads(obj_in["nickname_history"])
            assert len(history) == 1
            assert history[0]["name"] == "OldNick"
            assert isinstance(history[0]["retired_at"], float)
            # cardname 未传入，不应改
            assert "cardname" not in obj_in

    def test_update_person_info_no_history_when_name_unchanged(self):
        """nickname/cardname 与旧值相同时，不应写入历史。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 102
        mock_person.interaction_count = 1
        mock_person.nickname = "SameNick"
        mock_person.cardname = "SameCard"
        mock_person.nickname_history = None

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            asyncio.run(
                helper.update_person_info(
                    "telegram",
                    "user123",
                    nickname="SameNick",
                    cardname="SameCard",
                )
            )

            obj_in = helper.person_crud.update.call_args.args[1]
            assert obj_in["nickname"] == "SameNick"
            assert obj_in["cardname"] == "SameCard"
            # 名字没变，不应写历史
            assert "nickname_history" not in obj_in

    def test_update_person_info_empty_new_name_does_not_clear_current(self):
        """传入空字符串新名时不应清空当前名，也不应写入历史。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 103
        mock_person.interaction_count = 1
        mock_person.nickname = "ExistingNick"
        mock_person.cardname = "ExistingCard"
        mock_person.nickname_history = None

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            asyncio.run(
                helper.update_person_info(
                    "telegram", "user123", nickname="", cardname=""
                )
            )

            obj_in = helper.person_crud.update.call_args.args[1]
            # 空字符串 strip 后为 None，不应更新 nickname/cardname 字段
            assert "nickname" not in obj_in
            assert "cardname" not in obj_in
            assert "nickname_history" not in obj_in

    def test_append_name_history_dedupes_consecutive_same_name(self):
        """连续相同旧名不应重复写入历史。"""
        import json

        from src.core.utils.user_query_helper import _append_name_history

        existing = json.dumps([{"name": "Foo", "retired_at": 1.0}])
        result = _append_name_history(existing, "Foo", 2.0)
        history = json.loads(result)
        assert len(history) == 1
        assert history[0]["name"] == "Foo"
        assert history[0]["retired_at"] == 1.0

    def test_append_name_history_caps_max_entries(self):
        """历史条数超过上限时应丢弃最旧的。"""
        import json

        from src.core.utils.user_query_helper import _append_name_history

        # 预填 50 条
        existing = json.dumps(
            [{"name": f"n{i}", "retired_at": float(i)} for i in range(50)]
        )
        result = _append_name_history(existing, "new_old", 99.0, max_entries=50)
        history = json.loads(result)
        assert len(history) == 50
        # 最旧的 n0 应被丢弃
        assert history[0]["name"] == "n1"
        # 最新追加的应在末尾
        assert history[-1]["name"] == "new_old"
        assert history[-1]["retired_at"] == 99.0

    def test_append_name_history_ignores_empty_old_name(self):
        """空旧名不应入历史。"""
        import json

        from src.core.utils.user_query_helper import _append_name_history

        result = _append_name_history(None, "", 1.0)
        assert json.loads(result) == []
        result2 = _append_name_history(None, "   ", 1.0)
        assert json.loads(result2) == []

    def test_append_name_history_recovers_from_corrupt_json(self):
        """历史 JSON 损坏时应容错为空列表。"""
        import json

        from src.core.utils.user_query_helper import _append_name_history

        result = _append_name_history("not valid json {{{", "OldName", 1.0)
        history = json.loads(result)
        assert len(history) == 1
        assert history[0]["name"] == "OldName"



    @patch("src.core.utils.user_query_helper.QueryBuilder")
    def test_get_user_streams(self, mock_query_builder):
        """测试获取用户聊天流。"""
        import asyncio

        mock_streams = [MagicMock(), MagicMock()]
        mock_qb = MagicMock()
        mock_qb.filter.return_value = mock_qb
        mock_qb.order_by.return_value = mock_qb
        mock_qb.all = AsyncMock(return_value=mock_streams)
        mock_query_builder.return_value = mock_qb

        helper = UserQueryHelper()
        streams = asyncio.run(helper.get_user_streams("telegram", "user123"))

        assert streams == mock_streams

    @patch("src.core.utils.user_query_helper.QueryBuilder")
    def test_get_user_recent_messages(self, mock_query_builder):
        """测试获取用户最近消息。"""
        import asyncio

        mock_messages = [MagicMock(), MagicMock(), MagicMock()]
        mock_qb = MagicMock()
        mock_qb.filter.return_value = mock_qb
        mock_qb.order_by.return_value = mock_qb
        mock_qb.limit.return_value = mock_qb
        mock_qb.all = AsyncMock(return_value=mock_messages)
        mock_query_builder.return_value = mock_qb

        helper = UserQueryHelper()
        messages = asyncio.run(helper.get_user_recent_messages("telegram", "user123", limit=50))

        assert messages == mock_messages
        mock_qb.limit.assert_called_once_with(50)

    def test_enrich_message_with_person_info(self):
        """测试为消息补充用户信息。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.nickname = "TestUser"
        mock_person.cardname = "TestCard"
        mock_person.attitude = 75
        mock_person.interaction_count = 10

        mock_message = MagicMock()
        mock_message.person_id = "person123"
        mock_message.to_dict.return_value = {"message_id": "msg123"}

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)

            result = asyncio.run(helper.enrich_message_with_person_info(mock_message))

            assert result["user_nickname"] == "TestUser"
            assert result["user_cardname"] == "TestCard"
            assert result["user_attitude"] == 75
            assert result["user_interaction_count"] == 10

    def test_enrich_message_no_person_id(self):
        """测试为没有 person_id 的消息补充信息。"""
        import asyncio

        mock_message = MagicMock()
        mock_message.person_id = None
        mock_message.to_dict.return_value = {"message_id": "msg123"}

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            result = asyncio.run(helper.enrich_message_with_person_info(mock_message))

            # 应该返回原始字典，没有额外字段
            assert result == {"message_id": "msg123"}

    def test_update_user_impression(self):
        """测试更新用户印象。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 1

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            result = asyncio.run(
                helper.update_user_impression("telegram", "user123", "friendly user")
            )

            assert result is True
            helper.person_crud.update.assert_called_once()

    def test_update_user_impression_user_not_found(self):
        """测试更新不存在用户的印象。"""
        import asyncio

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=None)

            result = asyncio.run(
                helper.update_user_impression("telegram", "user123", "friendly user")
            )

            assert result is False

    def test_update_user_attitude(self):
        """测试更新用户态度。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 1
        mock_person.attitude = 50

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            new_attitude = asyncio.run(helper.update_user_attitude("telegram", "user123", 10))

            assert new_attitude == 60
            helper.person_crud.update.assert_called_once()

    def test_update_user_attitude_clamping(self):
        """测试态度评分的边界限制。"""
        import asyncio

        mock_person = MagicMock()
        mock_person.id = 1
        mock_person.attitude = 50

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=mock_person)
            helper.person_crud.update = AsyncMock()

            # 测试上限
            attitude1 = asyncio.run(helper.update_user_attitude("telegram", "user123", 100))
            assert attitude1 == 100

            # 测试下限
            mock_person.attitude = 50
            attitude2 = asyncio.run(helper.update_user_attitude("telegram", "user123", -100))
            assert attitude2 == 0

    def test_update_user_attitude_user_not_found(self):
        """测试更新不存在用户的态度。"""
        import asyncio

        with patch("src.core.utils.user_query_helper.CRUDBase"):
            helper = UserQueryHelper()
            helper.person_crud.get_by = AsyncMock(return_value=None)

            result = asyncio.run(helper.update_user_attitude("telegram", "user123", 10))

            assert result is None


class TestGetUserQueryHelper:
    """测试 get_user_query_helper 单例函数。"""

    def test_singleton(self):
        """测试单例模式。"""
        with patch("src.core.utils.user_query_helper.UserQueryHelper"):
            helper1 = get_user_query_helper()
            helper2 = get_user_query_helper()

            assert helper1 is helper2

    def test_singleton_persistence(self):
        """测试单例持久性。"""
        with patch("src.core.utils.user_query_helper.UserQueryHelper"):
            get_user_query_helper()
            # 全局变量应该被设置
            from src.core.utils.user_query_helper import _user_query_helper
            assert _user_query_helper is not None

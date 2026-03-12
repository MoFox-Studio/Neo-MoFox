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
            helper = get_user_query_helper()
            # 全局变量应该被设置
            from src.core.utils.user_query_helper import _user_query_helper
            assert _user_query_helper is not None

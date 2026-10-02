"""
配置模块单元测试
"""
import os
import sys
import json
import tempfile
import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from data.config import Config, hash_password, verify_password, is_password_hashed


class TestPasswordHashing:
    """测试密码哈希功能"""
    
    def test_hash_password(self):
        """测试密码哈希生成"""
        hashed = hash_password("test123")
        assert hashed.startswith("$pbkdf2-sha256$")
        assert len(hashed) > 50
    
    def test_verify_password_correct(self):
        """测试正确密码验证"""
        password = "mypassword"
        hashed = hash_password(password)
        assert verify_password(password, hashed) is True
    
    def test_verify_password_wrong(self):
        """测试错误密码验证"""
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False
    
    def test_verify_password_empty(self):
        """测试空密码验证"""
        assert verify_password("", "hash") is False
        assert verify_password("pass", "") is False
        assert verify_password("", "") is False
    
    def test_is_password_hashed(self):
        """测试密码哈希格式检查"""
        assert is_password_hashed("$pbkdf2-sha256$310000$abc$def") is True
        assert is_password_hashed("plaintext") is False
        assert is_password_hashed("") is True  # 空密码视为已哈希
    
    def test_different_hashes(self):
        """测试相同密码生成不同哈希（随机盐）"""
        h1 = hash_password("same")
        h2 = hash_password("same")
        assert h1 != h2  # 不同盐导致不同哈希
        assert verify_password("same", h1) is True
        assert verify_password("same", h2) is True


class TestConfig:
    """测试配置类"""
    
    def test_default_config(self):
        """测试默认配置加载"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(config_dir=tmpdir)
            assert config.llm.model is not None
            # 7860 是服务默认端口（19876 是进程锁端口，不是服务端口）
            assert config.web.port == 7860
    
    def test_config_update(self):
        """测试配置更新"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(config_dir=tmpdir)
            config.update("llm.model", "gpt-4")
            assert config.llm.model == "gpt-4"
            
            # 重新加载验证持久化
            config2 = Config(config_dir=tmpdir)
            assert config2.llm.model == "gpt-4"
    
    def test_config_validation(self):
        """测试配置验证"""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = Config(config_dir=tmpdir)
            # 测试无效端口
            with pytest.raises(ValueError):
                config.update("web.port", -1)
            # 测试有效端口
            config.update("web.port", 8080)
            assert config.web.port == 8080


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
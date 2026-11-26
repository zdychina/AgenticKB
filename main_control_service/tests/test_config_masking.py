"""config_masking 单元测试（BUG-05：掩码 + 路径配对合并）。"""
from __future__ import annotations

import yaml

from main_control_service.config_masking import (
    MASK_PREFIX,
    MaskMergeError,
    consume_bootstrap_line,
    is_sensitive_key,
    mask_structured,
    mask_value,
    mask_yaml_text,
    merge_masked_yaml,
    should_mask_value,
)


# ---------------------------------------------------------------------------
# 键归一化与值规则
# ---------------------------------------------------------------------------

def test_sensitive_key_normalization():
    assert is_sensitive_key("jwt_secret")
    assert is_sensitive_key("X-HW-APPKEY")      # 连字符+大写 → x_hw_appkey
    assert is_sensitive_key("X-HW-ID")
    assert is_sensitive_key("admin_password")
    assert is_sensitive_key("static_token")
    assert is_sensitive_key("api_key")
    assert is_sensitive_key("access_key")
    assert is_sensitive_key("Authorization")
    assert is_sensitive_key("token_ttl_seconds")  # 键敏感，但值是 int 不掩
    # 非敏感
    assert not is_sensitive_key("app_id")
    assert not is_sensitive_key("active_model")
    assert not is_sensitive_key("allowed_ips")
    assert not is_sensitive_key("token_url")      # 豁免：公开端点 URL
    assert not is_sensitive_key("search_url")


def test_should_mask_value_rules():
    assert should_mask_value("sk-aaaaaaaa1111")
    assert not should_mask_value(43200)            # int（token_ttl_seconds）
    assert not should_mask_value("short")          # <8
    assert not should_mask_value("")
    assert not should_mask_value(None)
    assert not should_mask_value(["list"])


def test_mask_value_suffix_and_env_ref():
    assert mask_value("sk-aaaaaaaa1111") == MASK_PREFIX + "1111"
    assert mask_value("${DSH_JWT_SECRET}") == MASK_PREFIX  # 引用形态不泄末4位


# ---------------------------------------------------------------------------
# mask_yaml_text：文本掩码保注释/引号/结构
# ---------------------------------------------------------------------------

_LLM_LIKE = """# 顶部注释
provider:
  active_model: "m1"
  headers: {}
  models:
    m1:
      model: "m1"
      api_key: "sk-aaaaaaaa1111"
      timeout: 300
    m2:
      model: "m2"
      api_key: sk-bbbbbbbb2222  # 行内尾注释
      headers:
        X-HW-ID: "hw-id-value-123"
embedding:
  api_key: local-tei-noauth-key
  dimensions: 1024
token_ttl_seconds: 43200
token_url: "https://oauth2.example.com/token"
"""


def test_mask_yaml_text_masks_each_secret_keeps_comments():
    masked, paths = mask_yaml_text(_LLM_LIKE)
    assert "sk-aaaaaaaa1111" not in masked
    assert "sk-bbbbbbbb2222" not in masked
    assert "hw-id-value-123" not in masked
    assert "local-tei-noauth-key" not in masked
    # 各自掩码、末 4 位可区分
    assert '"%s"' % (MASK_PREFIX + "1111") in masked
    assert '"%s"' % (MASK_PREFIX + "2222") in masked
    # 非敏感/豁免/短值/int 不掩
    assert "43200" in masked
    assert "https://oauth2.example.com/token" in masked
    assert "local-tei-noauth-key" not in masked
    # 注释与结构保留
    assert "# 顶部注释" in masked
    assert "# 行内尾注释" in masked
    assert "active_model:" in masked
    assert set(paths) == {
        "provider.models.m1.api_key",
        "provider.models.m2.api_key",
        "provider.models.m2.headers.X-HW-ID",
        "embedding.api_key",
    }
    # 输出仍是可解析 YAML 且值正确
    parsed = yaml.safe_load(masked)
    assert parsed["provider"]["models"]["m1"]["api_key"] == MASK_PREFIX + "1111"
    assert parsed["token_ttl_seconds"] == 43200


def test_mask_yaml_text_plain_value_keeps_inline_comment():
    masked, _ = mask_yaml_text(_LLM_LIKE)
    assert 'api_key: "%s2222"  # 行内尾注释' % MASK_PREFIX in masked


def test_mask_yaml_text_structured_consistency():
    """验证项 8：raw 掩码与结构化掩码同形。"""
    masked_text, text_paths = mask_yaml_text(_LLM_LIKE)
    masked_obj, obj_paths = mask_structured(yaml.safe_load(_LLM_LIKE))
    assert yaml.safe_load(masked_text) == masked_obj
    assert set(text_paths) == set(obj_paths)


def test_mask_yaml_text_crlf():
    text = _LLM_LIKE.replace("\n", "\r\n")
    masked, _ = mask_yaml_text(text)
    assert "sk-aaaaaaaa1111" not in masked
    parsed = yaml.safe_load(masked)
    assert parsed["provider"]["models"]["m1"]["api_key"] == MASK_PREFIX + "1111"
    assert "\r\n" in masked  # 行尾风格保留


def test_mask_yaml_text_block_scalar_skipped():
    text = 'secrets:\n  password: |\n    line1aaaaaaaa\n    line2bbbbbbbb\n'
    masked, paths = mask_yaml_text(text)
    assert paths == []  # 多行/块标量保守跳过，不破坏文件
    assert "line1aaaaaaaa" in masked


def test_mask_structured_deep_copies_input():
    obj = {"a": {"api_key": "sk-xxxxxxxx9999"}}
    masked, _ = mask_structured(obj)
    assert masked["a"]["api_key"] == MASK_PREFIX + "9999"
    assert obj["a"]["api_key"] == "sk-xxxxxxxx9999"  # 原对象（含缓存）不被改


# ---------------------------------------------------------------------------
# merge_masked_yaml：路径配对恢复
# ---------------------------------------------------------------------------

def test_merge_roundtrip_restores_disk_values():
    masked, _ = mask_yaml_text(_LLM_LIKE)
    merged, restored = merge_masked_yaml(masked, _LLM_LIKE)
    assert set(restored) == {
        "provider.models.m1.api_key",
        "provider.models.m2.api_key",
        "provider.models.m2.headers.X-HW-ID",
        "embedding.api_key",
    }
    assert yaml.safe_load(merged) == yaml.safe_load(_LLM_LIKE)
    assert "sk-aaaaaaaa1111" in merged


def test_merge_survives_reorder_and_deletion():
    """增删/重排后按路径恢复（行序配对必然错位的场景）。"""
    masked, _ = mask_yaml_text(_LLM_LIKE)
    lines = masked.split("\n")
    m1_start = next(i for i, ln in enumerate(lines) if "    m1:" in ln)
    m2_start = next(i for i, ln in enumerate(lines) if "    m2:" in ln)
    m2_end = next(i for i, ln in enumerate(lines) if "embedding:" in ln)
    m1_block = lines[m1_start:m2_start]
    m2_block = lines[m2_start:m2_end]
    # 交换两个模型块 + 删除 embedding 块（含其 api_key）
    reordered = lines[:m1_start] + m2_block + m1_block + lines[m2_end + 3:]
    merged, restored = merge_masked_yaml("\n".join(reordered), _LLM_LIKE)
    parsed = yaml.safe_load(merged)
    assert parsed["provider"]["models"]["m1"]["api_key"] == "sk-aaaaaaaa1111"
    assert parsed["provider"]["models"]["m2"]["api_key"] == "sk-bbbbbbbb2222"
    assert "embedding" not in parsed
    assert set(restored) == {
        "provider.models.m1.api_key",
        "provider.models.m2.api_key",
        "provider.models.m2.headers.X-HW-ID",
    }


def test_merge_rotation_passes_new_value_through():
    masked, _ = mask_yaml_text(_LLM_LIKE)
    rotated = masked.replace(MASK_PREFIX + "1111", "sk-NEW-rotated9999")
    merged, restored = merge_masked_yaml(rotated, _LLM_LIKE)
    parsed = yaml.safe_load(merged)
    assert parsed["provider"]["models"]["m1"]["api_key"] == "sk-NEW-rotated9999"
    assert "provider.models.m1.api_key" not in restored


def test_merge_non_secret_edit_keeps_secrets():
    masked, _ = mask_yaml_text(_LLM_LIKE)
    edited = masked.replace("43200", "3600")
    merged, restored = merge_masked_yaml(edited, _LLM_LIKE)
    parsed = yaml.safe_load(merged)
    assert parsed["token_ttl_seconds"] == 3600
    assert parsed["provider"]["models"]["m1"]["api_key"] == "sk-aaaaaaaa1111"


def test_merge_rejects_mask_on_new_key():
    masked, _ = mask_yaml_text(_LLM_LIKE)
    with_new = masked + 'extra_api_key: "%sabcd"\n' % MASK_PREFIX
    try:
        merge_masked_yaml(with_new, _LLM_LIKE)
    except MaskMergeError as e:
        assert "extra_api_key" in e.paths[0]
    else:
        raise AssertionError("expected MaskMergeError")


def test_merge_rejects_structure_change():
    masked, _ = mask_yaml_text(_LLM_LIKE)
    as_list = masked.replace("  models:\n", "  models:\n    - x\n", 1)
    try:
        merge_masked_yaml(as_list, _LLM_LIKE)
    except (MaskMergeError, yaml.YAMLError):
        pass
    else:
        raise AssertionError("expected MaskMergeError or YAMLError")


def test_merge_rejects_duplicate_keys():
    text = 'a:\n  api_key: k11111111\n  api_key: k22222222\n'
    try:
        mask_yaml_text(text)
    except MaskMergeError:
        pass
    else:
        raise AssertionError("expected MaskMergeError on duplicate key")


def test_merge_rejects_anchors():
    text = "base: &b\n  api_key: k11111111\nother: *b\n"
    try:
        mask_yaml_text(text)
    except MaskMergeError:
        pass
    else:
        raise AssertionError("expected MaskMergeError on anchors/aliases")


def test_merge_quotes_special_characters():
    disk = 'password: "p@ss \\"q\\": #x-1234"\n'
    incoming = 'password: "%s1234"\n' % MASK_PREFIX
    merged, restored = merge_masked_yaml(incoming, disk)
    assert yaml.safe_load(merged)["password"] == 'p@ss "q": #x-1234'
    assert restored == ["password"]


def test_merge_returns_incoming_unchanged_when_no_masks():
    merged, restored = merge_masked_yaml(_LLM_LIKE, _LLM_LIKE)
    assert merged == _LLM_LIKE
    assert restored == []


# ---------------------------------------------------------------------------
# consume_bootstrap_line
# ---------------------------------------------------------------------------

_AUTH_LIKE = """# Authentication configuration.
enabled: true
jwt_secret: abcdef1234567890abcdef1234567890
bootstrap:
  # First deployment generates this once, then flips the flag to false.
  initialize_on_deploy: true
  admin_password: "0123456789abcdef0123456789abcdef01234567"
"""


def test_consume_bootstrap_line_removes_only_that_line():
    out, removed = consume_bootstrap_line(_AUTH_LIKE)
    assert removed is True
    assert "admin_password" not in out
    assert "# First deployment generates this once" in out
    assert "jwt_secret: abcdef" in out
    parsed = yaml.safe_load(out)
    assert parsed["bootstrap"] == {"initialize_on_deploy": True}


def test_consume_bootstrap_line_idempotent():
    once, _ = consume_bootstrap_line(_AUTH_LIKE)
    twice, removed = consume_bootstrap_line(once)
    assert removed is False
    assert twice == once

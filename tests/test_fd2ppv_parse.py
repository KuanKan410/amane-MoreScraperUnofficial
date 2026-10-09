"""esl1.fd2ppv 的单元测试（只测纯逻辑；浏览器相关靠实机验证）。"""


def test_descriptor_is_well_formed():
    desc = PLUGIN.Plugin.descriptor()
    assert desc.id == "esl1.fd2ppv"
    assert desc.content_types == frozenset({"fc2"})
    assert "film_metadata" in desc.capabilities or PLUGIN.SourceCapability.FILM_METADATA in desc.capabilities
    assert "title" in desc.metadata_fields
    assert "extrafanart" in desc.metadata_fields


def test_config_defaults_are_sane():
    cfg = PLUGIN.Fd2PpvConfig()
    assert cfg.base_url == "https://fd2ppv.cc"
    assert cfg.detail_template == "{base}/articles/{digits}"
    assert cfg.browser_fallback is True
    assert cfg.browser_headless is False
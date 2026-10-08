# -*- coding: utf-8 -*-
"""AuxiliaryClient._resolve 的 vision 任务模型解析。

vision_model 未设置/为空 = 任务禁用（不回落主模型）：本部署主模型是纯文本
模型，带 image_url 的消息只会得到 400 + 误导性重试（2026-09-29 网关日志）。
"""
from core.agent.auxiliary_client import AuxiliaryClient


def _resolve_model(config, task="vision"):
    aux = AuxiliaryClient(base_url="http://x", api_key="k",
                          model="main-model", config=config)
    model, _ = aux._resolve(task)
    return model


def test_vision_model_unset_disables_task():
    # 键不存在 → 禁用，绝不回落主模型
    assert _resolve_model({"model": "main-model"}) == ""


def test_vision_model_none_disables_task():
    # YAML `vision_model:`（None，最常见写法）→ 禁用
    assert _resolve_model({"model": "main-model", "vision_model": None}) == ""


def test_vision_model_empty_string_disables_task():
    # 显式 "" → 禁用（原语义保留）
    assert _resolve_model({"model": "main-model", "vision_model": ""}) == ""


def test_vision_model_set_is_used():
    assert _resolve_model({"model": "main-model",
                           "vision_model": "glm-4v"}) == "glm-4v"


def test_other_tasks_still_fall_back_to_main_model():
    # 非 vision 任务（无 top_key 映射）保留回落主模型的默认
    assert _resolve_model({"model": "main-model"}, task="title") == "main-model"

from backend.gpu_monitor import GpuMetrics
from backend.autopilot import AutopilotSettings, evaluate


def make_metrics(temp_c=50.0, vram_used_mb=1000.0, vram_total_mb=8000.0, util_pct=5.0):
    return GpuMetrics(temp_c=temp_c, vram_used_mb=vram_used_mb, vram_total_mb=vram_total_mb, util_pct=util_pct)


def test_default_history_retention_is_30_days():
    settings = AutopilotSettings()
    assert settings.history_retention_days == 30


def test_no_rules_triggered_returns_no_pause():
    settings = AutopilotSettings()
    decision = evaluate(make_metrics(), jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is False
    assert decision.reasons == ()


def test_temp_above_pause_threshold_triggers_pause():
    settings = AutopilotSettings(temp_rule_enabled=True, pause_temp_c=80.0, resume_temp_c=72.0)
    decision = evaluate(make_metrics(temp_c=85.0), jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is True
    assert "85" in decision.reasons[0]


def test_temp_hysteresis_keeps_paused_until_resume_threshold():
    settings = AutopilotSettings(temp_rule_enabled=True, pause_temp_c=80.0, resume_temp_c=72.0)
    # already paused, temp dropped below pause_temp_c but not yet below resume_temp_c
    decision = evaluate(make_metrics(temp_c=75.0), jobs_since_resume=0, currently_paused=True, settings=settings)
    assert decision.should_pause is True


def test_temp_hysteresis_resumes_once_below_resume_threshold():
    settings = AutopilotSettings(temp_rule_enabled=True, pause_temp_c=80.0, resume_temp_c=72.0)
    decision = evaluate(make_metrics(temp_c=70.0), jobs_since_resume=0, currently_paused=True, settings=settings)
    assert decision.should_pause is False


def test_temp_rule_disabled_never_pauses_on_temp():
    settings = AutopilotSettings(temp_rule_enabled=False, pause_temp_c=80.0)
    decision = evaluate(make_metrics(temp_c=99.0), jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is False


def test_low_free_vram_triggers_pause():
    settings = AutopilotSettings(vram_rule_enabled=True, min_free_vram_mb=1024.0)
    decision = evaluate(
        make_metrics(vram_used_mb=7800.0, vram_total_mb=8000.0),
        jobs_since_resume=0, currently_paused=False, settings=settings,
    )
    assert decision.should_pause is True
    assert "VRAM" in decision.reasons[0]


def test_vram_hysteresis_keeps_paused_until_resume_threshold():
    settings = AutopilotSettings(vram_rule_enabled=True, min_free_vram_mb=1024.0, resume_free_vram_mb=1536.0)
    # already paused, freed up a bit but not yet past resume_free_vram_mb
    decision = evaluate(
        make_metrics(vram_used_mb=6800.0, vram_total_mb=8000.0),  # 1200MB free
        jobs_since_resume=0, currently_paused=True, settings=settings,
    )
    assert decision.should_pause is True


def test_vram_hysteresis_resumes_once_above_resume_threshold():
    settings = AutopilotSettings(vram_rule_enabled=True, min_free_vram_mb=1024.0, resume_free_vram_mb=1536.0)
    decision = evaluate(
        make_metrics(vram_used_mb=6000.0, vram_total_mb=8000.0),  # 2000MB free
        jobs_since_resume=0, currently_paused=True, settings=settings,
    )
    assert decision.should_pause is False


def test_vram_rule_disabled_never_pauses_on_vram():
    settings = AutopilotSettings(vram_rule_enabled=False, min_free_vram_mb=1024.0)
    decision = evaluate(
        make_metrics(vram_used_mb=7999.0, vram_total_mb=8000.0),
        jobs_since_resume=0, currently_paused=False, settings=settings,
    )
    assert decision.should_pause is False


def test_job_count_at_or_above_max_triggers_pause():
    settings = AutopilotSettings(job_count_rule_enabled=True, max_jobs_before_pause=20)
    decision = evaluate(make_metrics(), jobs_since_resume=20, currently_paused=False, settings=settings)
    assert decision.should_pause is True
    assert "20" in decision.reasons[0]


def test_job_count_rule_disabled_never_pauses_on_count():
    settings = AutopilotSettings(job_count_rule_enabled=False, max_jobs_before_pause=1)
    decision = evaluate(make_metrics(), jobs_since_resume=999, currently_paused=False, settings=settings)
    assert decision.should_pause is False


def test_missing_gpu_data_does_not_trigger_temp_or_vram_rules():
    settings = AutopilotSettings()
    metrics = GpuMetrics(temp_c=None, vram_used_mb=None, vram_total_mb=None, util_pct=None)
    decision = evaluate(metrics, jobs_since_resume=0, currently_paused=False, settings=settings)
    assert decision.should_pause is False


def test_update_from_dict_mutates_matching_fields_only():
    settings = AutopilotSettings()
    settings.update_from_dict({"pause_temp_c": 90.0, "not_a_real_field": 123})
    assert settings.pause_temp_c == 90.0
    assert not hasattr(settings, "not_a_real_field")


def test_update_from_dict_coerces_values_to_the_declared_type():
    settings = AutopilotSettings()
    settings.update_from_dict({"pause_temp_c": "90", "max_jobs_before_pause": "5", "temp_rule_enabled": True})
    assert settings.pause_temp_c == 90.0
    assert isinstance(settings.pause_temp_c, float)
    assert settings.max_jobs_before_pause == 5
    assert isinstance(settings.max_jobs_before_pause, int)


def test_update_from_dict_ignores_a_value_that_cannot_be_coerced():
    settings = AutopilotSettings(pause_temp_c=80.0)
    settings.update_from_dict({"pause_temp_c": "not-a-number"})
    assert settings.pause_temp_c == 80.0


def test_update_from_dict_cannot_clobber_its_own_method():
    # A malicious or malformed payload of {"update_from_dict": 1} used to
    # setattr over the method itself via plain hasattr()/setattr(), crashing
    # every later call (spec §26.2).
    settings = AutopilotSettings()
    settings.update_from_dict({"update_from_dict": 1})
    settings.update_from_dict({"pause_temp_c": 77.0})
    assert settings.pause_temp_c == 77.0


def test_multiple_reasons_all_reported():
    settings = AutopilotSettings(
        temp_rule_enabled=True, pause_temp_c=80.0, vram_rule_enabled=True, min_free_vram_mb=1024.0
    )
    decision = evaluate(
        make_metrics(temp_c=90.0, vram_used_mb=7800.0, vram_total_mb=8000.0),
        jobs_since_resume=0, currently_paused=False, settings=settings,
    )
    assert len(decision.reasons) == 2

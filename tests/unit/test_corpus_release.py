import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from tools import corpus_legacy_suites, corpus_runs, replay_scan_inputs
from tools.corpus_artifacts import digest
from tools.corpus_runs import (
    RELEASE_JOBS,
    RELEASE_MATRIX,
    REQUIRED_RELEASE_FLAGS,
    SHA_FLAGS,
    ReleaseJob,
    ReleasePin,
    ReleasePlan,
    check,
    observation_ids_sha256,
    register,
    release_arguments,
    release_population,
    release_sidecar,
    verify,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/corpus_check"


def corpus_at(tmp_path: Path) -> Path:
    corpus = tmp_path / "corpus"
    shutil.copytree(FIXTURE, corpus)
    (corpus / "INDEX.json").write_text("{}")
    (corpus / "documents").mkdir()
    (corpus / "documents/index.json").write_text("{}")
    return corpus


def job_at(corpus: Path, identity: str = "external-delivery") -> ReleaseJob:
    path = corpus / "documents/index.json"
    sha = digest(path.read_bytes())
    args = tuple(
        value
        for flag in REQUIRED_RELEASE_FLAGS.get(identity, ())
        for value in (flag, sha if flag in SHA_FLAGS else str(path))
    )
    rows = {"delivery": None} if identity == "external-delivery" else {"fixture": None}
    return ReleaseJob(
        id=identity,
        suite=RELEASE_JOBS[identity][0],
        args=args,
        pins=(ReleasePin(path=str(path), sha256=sha),),
        expected_observations=len(rows),
        expected_ids_sha256=observation_ids_sha256(rows),
    )


def plan_at(tmp_path: Path, corpus: Path, *jobs: ReleaseJob) -> tuple[Path, str]:
    path = tmp_path / "plan.json"
    plan = ReleasePlan(
        version=1,
        index_sha256=digest((corpus / "INDEX.json").read_bytes()),
        jobs=tuple(jobs),
    )
    path.write_text(plan.model_dump_json())
    return path, digest(path.read_bytes())


def test_release_delivery_runs_real_subprocesses_and_compares_verified_children(
    tmp_path: Path,
) -> None:
    corpus = corpus_at(tmp_path)
    plan, pin = plan_at(tmp_path, corpus, job_at(corpus))
    before, after = tmp_path / "before.json", tmp_path / "after.json"

    assert check(corpus, before, ("delivery",), plan, pin, mode="baseline") == 0
    assert (
        check(
            corpus,
            after,
            ("delivery",),
            plan,
            pin,
            mode="compare",
            reference=before,
            reference_sha256=digest(before.read_bytes()),
        )
        == 0
    )

    baseline, current = [json.loads(path.read_bytes()) for path in (before, after)]
    assert baseline["status"] == "recorded" and current["status"] == "passed"
    assert current["qualified"] is True and current["profiles"] == {"delivery": "passed"}
    child = release_sidecar(after) / "external-delivery"
    assert verify(child)["observations"] == 1
    report = json.loads((child / "result.json").read_bytes())
    assert set(report["observations"]["delivery"]["actual"]) == {
        "confirm",
        "dismiss",
        "retry_snapshot",
    }
    assert current["children"]["external-delivery"]["manifest_sha256"] == digest(
        (child / "manifest.json").read_bytes()
    )
    assert current["children"]["external-delivery"]["comparison"] == "same"
    assert "cases" not in current and "observations" not in current
    with pytest.raises(ValueError, match="new and outside"):
        check(corpus, after, ("delivery",), plan, pin, mode="baseline")


@pytest.mark.parametrize(
    "kind", ["plan", "index", "artifact", "schema", "unknown-job", "duplicate"]
)
def test_release_rejects_changed_pins_and_noncanonical_plans(tmp_path: Path, kind: str) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus)
    plan, pin = plan_at(tmp_path, corpus, job)
    if kind == "plan":
        plan.write_text(plan.read_text() + " ")
    elif kind == "index":
        (corpus / "INDEX.json").write_text('{"changed":true}')
    elif kind == "artifact":
        (corpus / "documents/index.json").write_text('{"changed":true}')
    else:
        raw = json.loads(plan.read_bytes())
        if kind == "schema":
            raw["unexpected"] = True
        elif kind == "unknown-job":
            raw["jobs"][0]["id"] = "sources"
        else:
            raw["jobs"].append(raw["jobs"][0])
        plan.write_text(json.dumps(raw))
        pin = digest(plan.read_bytes())
    output = tmp_path / "result.json"

    assert check(corpus, output, ("delivery",), plan, pin, mode="baseline") == 1
    assert json.loads(output.read_bytes())["qualified"] is False
    assert not (release_sidecar(output) / "external-delivery").exists()


@pytest.mark.parametrize("kind", ["job", "plan", "artifact", "index"])
def test_missing_release_prerequisites_are_not_run(tmp_path: Path, kind: str) -> None:
    corpus = corpus_at(tmp_path)
    plan, pin = plan_at(tmp_path, corpus, *(() if kind == "job" else (job_at(corpus),)))
    if kind == "plan":
        plan.unlink()
    elif kind == "artifact":
        (corpus / "documents/index.json").unlink()
    elif kind == "index":
        (corpus / "INDEX.json").unlink()
    output = tmp_path / "result.json"

    assert check(corpus, output, ("delivery",), plan, pin, mode="baseline") == 2
    assert json.loads(output.read_bytes())["status"] == "not_run"


@pytest.mark.parametrize(
    "args",
    [
        ("--output", "escape"),
        ("--out=escape",),
        ("--profiles", "extraction"),
        ("--prof=extraction",),
        ("--mode", "library"),
        ("--",),
        ("--unknown", "value"),
    ],
)
def test_release_cannot_override_owned_flags_or_enable_other_work(
    tmp_path: Path, args: tuple[str, ...]
) -> None:
    corpus = corpus_at(tmp_path)
    identity = "word" if args[:1] == ("--mode",) else "external-delivery"
    job = job_at(corpus, identity).model_copy(update={"args": args})

    with pytest.raises(ValueError):
        release_arguments(job)


def test_release_arguments_require_pinned_paths_and_exact_sha(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus, "odt")
    with pytest.raises(ValueError, match="no explicit pin"):
        release_arguments(job.model_copy(update={"args": ("--cases", str(tmp_path / "outside"))}))
    with pytest.raises(ValueError, match="SHA differs"):
        release_arguments(job.model_copy(update={"args": (*job.args[:-1], "0" * 64)}))
    with pytest.raises(ValueError, match="required pinned"):
        release_arguments(job.model_copy(update={"args": ()}))


@pytest.mark.parametrize("missing", ["missing_inputs", "complete_population", "empty", "cost"])
def test_nonempty_or_successful_child_does_not_prove_required_population(
    tmp_path: Path, missing: str
) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus, "cost" if missing == "cost" else "odt")
    report: dict[str, Any] = {"observations": {"fixture": {}}}
    if missing == "missing_inputs":
        report["missing_inputs"] = ["absent"]
    elif missing == "complete_population":
        report["complete_population"] = False
    elif missing == "empty":
        report["observations"] = {}

    assert release_population(job, {"status": "recorded"}, report) == "not_run"


def test_release_preserves_failure_over_missing_inputs(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus, "odt")
    report: dict[str, Any] = {
        "observations": {},
        "missing_inputs": ["absent"],
        "errors": {"file": "ValueError: broken"},
    }
    assert release_population(job, {"status": "failed"}, report) == "failed"
    assert release_population(job, {"status": "recorded"}, report) == "failed"
    report["errors"] = {"file": "FileNotFoundError: absent"}
    report["read_inputs"] = {"file": None}
    assert release_population(job, {"status": "failed"}, report) == "not_run"
    report["changed_inputs"] = ["file"]
    assert release_population(job, {"status": "failed"}, report) == "failed"


@pytest.mark.parametrize("compare", [False, True])
def test_real_scan_child_initial_absence_is_not_run_even_with_comparison_removals(
    tmp_path: Path, compare: bool
) -> None:
    corpus = corpus_at(tmp_path)
    manifest = tmp_path / "scan.json"
    manifest.write_text(
        json.dumps({"cases": {"absent.pdf": {"sha256": "0" * 64, "family": "scan"}}})
    )
    pin = digest(manifest.read_bytes())
    reference = None
    reference_pin = None
    if compare:
        reference = tmp_path / "before.json"
        reference.write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "case_manifest_sha256": pin,
                    "observations": {f"absent.pdf/cover={cover}": {} for cover in (False, True)},
                }
            )
        )
        reference_pin = digest(reference.read_bytes())
    output = tmp_path / "scan-result.json"

    assert replay_scan_inputs.run(corpus, manifest, pin, output, reference, reference_pin) == 1

    report = json.loads(output.read_bytes())
    assert report["status"] == "failed" and report["read_inputs"] == {"absent.pdf": None}
    assert "missing_inputs" not in report and not report["observations"]
    assert release_population(job_at(corpus, "scan"), {"status": "failed"}, report) == "not_run"
    report["errors"]["absent.pdf"] = "An opaque exception description"
    assert release_population(job_at(corpus, "scan"), {"status": "failed"}, report) == "not_run"


@pytest.mark.parametrize("suite", ["pdf", "word", "odt"])
def test_raw_child_absence_uses_initial_input_contract_not_error_text(
    tmp_path: Path, suite: str
) -> None:
    corpus = corpus_at(tmp_path)
    report: dict[str, Any] = {
        "status": "failed",
        "observations": {},
        "errors": {"absent": "Opaque failure"},
        "read_inputs": {"absent": None},
        "changes": ["absent"],
    }
    if suite == "odt":
        report["errors"]["population"] = "No successfully observed ODT leaves"
    assert release_population(job_at(corpus, suite), {"status": "failed"}, report) == "not_run"
    report["errors"]["present"] = "Corrupt input"
    report["read_inputs"]["present"] = "a" * 64
    assert release_population(job_at(corpus, suite), {"status": "failed"}, report) == "failed"


def test_known_input_becoming_absent_or_nonmissing_comparison_change_stays_failed(
    tmp_path: Path,
) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus, "pdf")
    report: dict[str, Any] = {
        "status": "failed",
        "observations": {},
        "errors": {"known": "FileNotFoundError"},
        "read_inputs": {"known": "a" * 64},
        "changed_inputs": ["known"],
    }
    assert release_population(job, {"status": "failed"}, report) == "failed"
    report.update(read_inputs={"known": None}, changed_inputs=[], changes=["different"])
    assert release_population(job, {"status": "failed"}, report) == "failed"


def test_real_legacy_not_run_error_description_remains_not_run(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    output = tmp_path / "senate.json"

    assert corpus_legacy_suites.run(corpus, output, kind="senate") == 2

    report = json.loads(output.read_bytes())
    assert report["status"] == "not_run" and report["error"]
    assert release_population(job_at(corpus, "senate"), {"status": "not_run"}, report) == "not_run"
    assert release_population(job_at(corpus, "senate"), {"status": "failed"}, report) == "failed"


def test_release_checks_population_identity_even_when_count_matches(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    assert (
        release_population(
            job_at(corpus, "odt"), {"status": "recorded"}, {"observations": {"different": 1}}
        )
        == "failed"
    )


def test_release_missing_expected_population_is_not_run(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus, "odt").model_copy(update={"expected_observations": 2})
    assert (
        release_population(job, {"status": "recorded"}, {"observations": {"fixture": 1}})
        == "not_run"
    )


def test_release_profiles_require_the_whole_frozen_matrix(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    plan, pin = plan_at(tmp_path, corpus)
    output = tmp_path / "result.json"

    assert check(corpus, output, tuple(RELEASE_MATRIX), plan, pin, mode="baseline") == 2
    result = json.loads(output.read_bytes())
    assert set(result["children"]) == set(RELEASE_JOBS)
    assert result["profiles"] == dict.fromkeys(RELEASE_MATRIX, "not_run")


def test_release_semantic_comparison_rejects_changed_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus).model_copy(
        update={
            "expected_observations": 1,
            "expected_ids_sha256": observation_ids_sha256({"fixture": None}),
        }
    )
    plan, pin = plan_at(tmp_path, corpus, job)
    answer = [1]

    def fake(
        root: Path, identity: str, suite: str, corpus: Path, extra: list[str]
    ) -> dict[str, Any]:
        source = root / "fixture.json"
        source.write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "observations": {"fixture": answer[0]},
                    "read_inputs": {"source": "a"},
                }
            )
        )
        return register(root, identity, suite, source)

    monkeypatch.setattr(corpus_runs, "execute", fake)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    assert check(corpus, before, ("delivery",), plan, pin, mode="baseline") == 0
    answer[0] = 2

    assert (
        check(
            corpus,
            after,
            ("delivery",),
            plan,
            pin,
            mode="compare",
            reference=before,
            reference_sha256=digest(before.read_bytes()),
        )
        == 1
    )
    assert (
        json.loads(after.read_bytes())["children"]["external-delivery"]["comparison"] == "changed"
    )


@pytest.mark.parametrize("kind", ["summary", "missing", "child"])
def test_release_rebinds_reference_after_child_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus).model_copy(
        update={"expected_ids_sha256": observation_ids_sha256({"fixture": None})}
    )
    plan, pin = plan_at(tmp_path, corpus, job)
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    mutate = [False]

    def fake(
        root: Path, identity: str, suite: str, corpus: Path, extra: list[str]
    ) -> dict[str, Any]:
        answer = 2 if mutate[0] and kind == "child" else 1
        source = root / "fixture.json"
        source.write_text(
            json.dumps(
                {
                    "status": "recorded",
                    "observations": {"fixture": answer},
                    "read_inputs": {"source": "a"},
                }
            )
        )
        child = register(root, identity, suite, source)
        if mutate[0]:
            if kind == "summary":
                before.write_bytes(before.read_bytes() + b"\n")
            elif kind == "missing":
                before.unlink()
            else:
                directory = release_sidecar(before) / identity
                result_path = directory / "result.json"
                result = json.loads(result_path.read_bytes())
                result["observations"]["fixture"] = answer
                result_path.write_text(json.dumps(result))
                manifest_path = directory / "manifest.json"
                manifest = json.loads(manifest_path.read_bytes())
                manifest["artifacts"]["result.json"] = {
                    "sha256": digest(result_path.read_bytes()),
                    "bytes": result_path.stat().st_size,
                }
                manifest_path.write_text(json.dumps(manifest))
                (directory / "manifest.sha256").write_text(digest(manifest_path.read_bytes()))
        return child

    monkeypatch.setattr(corpus_runs, "execute", fake)
    assert check(corpus, before, ("delivery",), plan, pin, mode="baseline") == 0
    reference_pin = digest(before.read_bytes())
    mutate[0] = True

    assert (
        check(
            corpus,
            after,
            ("delivery",),
            plan,
            pin,
            mode="compare",
            reference=before,
            reference_sha256=reference_pin,
        )
        == 1
    )
    result = json.loads(after.read_bytes())
    assert result["status"] == "failed" and result["qualified"] is False


def test_release_failed_child_wins_over_an_absent_required_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = corpus_at(tmp_path)
    plan, pin = plan_at(tmp_path, corpus, job_at(corpus))

    def fail(
        root: Path, identity: str, suite: str, corpus: Path, extra: list[str]
    ) -> dict[str, Any]:
        source = root / "failed.json"
        source.write_text(
            '{"status":"failed","observations":{"fixture":1},"errors":{"file":"ValueError: broken"}}'
        )
        return register(root, identity, suite, source)

    monkeypatch.setattr(corpus_runs, "execute", fail)
    output = tmp_path / "result.json"

    assert check(corpus, output, ("comparison", "delivery"), plan, pin, mode="baseline") == 1
    result = json.loads(output.read_bytes())
    assert result["profiles"] == {"comparison": "not_run", "delivery": "failed"}


@pytest.mark.parametrize("kind", ["hash", "child", "unqualified"])
def test_release_rejects_corrupt_or_incomplete_reference_before_new_children(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    corpus = corpus_at(tmp_path)
    plan, pin = plan_at(tmp_path, corpus, job_at(corpus))
    baseline, output = tmp_path / "baseline.json", tmp_path / "result.json"
    assert check(corpus, baseline, ("delivery",), plan, pin, mode="baseline") == 0
    reference_pin = digest(baseline.read_bytes())
    if kind == "hash":
        reference_pin = "0" * 64
    elif kind == "child":
        (release_sidecar(baseline) / "external-delivery/result.json").write_text("{}")
    else:
        data = json.loads(baseline.read_bytes())
        data["qualified"] = False
        baseline.write_text(json.dumps(data))
        reference_pin = digest(baseline.read_bytes())

    def unexpected(
        root: Path, identity: str, suite: str, corpus: Path, extra: list[str]
    ) -> dict[str, Any]:
        raise AssertionError("An invalid reference must stop before any new child")

    monkeypatch.setattr(corpus_runs, "execute", unexpected)
    assert (
        check(
            corpus,
            output,
            ("delivery",),
            plan,
            pin,
            mode="compare",
            reference=baseline,
            reference_sha256=reference_pin,
        )
        == 1
    )
    assert not release_sidecar(output).exists()


@pytest.mark.parametrize("kind", ["pin", "index", "credential"])
def test_release_does_not_read_aliased_or_credential_inputs(tmp_path: Path, kind: str) -> None:
    corpus = corpus_at(tmp_path)
    job = job_at(corpus)
    outside = tmp_path / (".env" if kind == "credential" else "outside.json")
    outside.write_text("outside data")
    if kind == "index":
        (corpus / "INDEX.json").unlink()
        (corpus / "INDEX.json").symlink_to(outside)
    else:
        alias = tmp_path / "alias.json"
        alias.symlink_to(outside)
        job = job.model_copy(
            update={
                "pins": (
                    ReleasePin(
                        path=str(outside if kind == "credential" else alias),
                        sha256=digest(outside.read_bytes()),
                    ),
                )
            }
        )
    plan, pin = plan_at(tmp_path, corpus, job)
    output = tmp_path / "result.json"

    assert check(corpus, output, ("delivery",), plan, pin, mode="baseline") == 1
    assert not (release_sidecar(output) / "external-delivery").exists()


def test_release_output_boundaries_and_reference_contract(tmp_path: Path) -> None:
    corpus = corpus_at(tmp_path)
    plan, pin = plan_at(tmp_path, corpus, job_at(corpus))
    output = tmp_path / "result.json"
    with pytest.raises(ValueError, match="new and outside"):
        check(corpus, corpus / "result.json", ("delivery",), plan, pin, mode="baseline")
    with pytest.raises(ValueError, match="pinned release reference"):
        check(corpus, output, ("delivery",), plan, pin, mode="compare")
    alias = tmp_path / "alias"
    alias.symlink_to(corpus, target_is_directory=True)
    with pytest.raises(ValueError, match="new and outside"):
        check(corpus, alias / "result.json", ("delivery",), plan, pin, mode="baseline")
    release_sidecar(output).mkdir()
    with pytest.raises(ValueError, match="new and outside"):
        check(corpus, output, ("delivery",), plan, pin, mode="baseline")


def test_release_cli_requires_explicit_plan_and_mode(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.corpus_check",
            "--corpus",
            str(FIXTURE),
            "--output",
            str(tmp_path / "result.json"),
            "--suite",
            "release",
            "--mode",
            "baseline",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2 and "explicit plan" in result.stderr
    assert not (tmp_path / "result.json").exists()


@pytest.mark.parametrize("suite", ["manifest", "external", "release"])
def test_make_corpus_check_preserves_old_commands_and_exposes_pinned_release(suite: str) -> None:
    command = [
        "make",
        "-n",
        "corpus-check",
        "CORPUS=fixture",
        "CORPUS_OUTPUT=new.json",
        f"CORPUS_SUITE={suite}",
    ]
    if suite != "manifest":
        command.append("CORPUS_MODE=baseline")
    if suite == "release":
        command.extend(["CORPUS_PLAN=plan.json", "CORPUS_PLAN_SHA256=" + "a" * 64])

    result = subprocess.run(command, capture_output=True, text=True, check=False)

    assert result.returncode == 0 and f'--suite "{suite}"' in result.stdout
    assert ('--plan "plan.json"' in result.stdout) == (suite == "release")

"""The dashboard's derived states, held to the locked Phase 3 rules.

Each test names the rule it checks (docs/design/deploypro-phase3-ux-states.md)
so a failure says which promise the page broke, not just which line changed.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from deploypro.domain.models import DeploymentStatus, EnvTarget, JobStatus
from deploypro.web import views
from tests import fakes

S = DeploymentStatus


def dep(number, status=S.READY, ref="main", **kwargs):
    base = dict(number=number, short_id=f"blog-{number:08x}", status=status, git_ref=ref)
    if status is not S.READY:
        base.setdefault("container_id", None)
    if status in (S.QUEUED,):
        base.update(started_at=None, built_at=None, ready_at=None, finished_at=None)
    if status is S.BUILDING:
        base.update(built_at=None, ready_at=None, finished_at=None)
    if status is S.DEPLOYING:
        base.update(ready_at=None, finished_at=None)
    base.update(kwargs)
    return fakes.deployment(**base)


class TestMarks:
    def test_every_deployment_status_has_its_own_shape(self):
        shapes = [m.shape for m in views.DEPLOYMENT_MARKS.values()]
        assert len(set(shapes)) == 6

    def test_blue_is_never_a_status_colour(self):
        """Phase 4 C1: blue identifies DeployPro actions, never a state."""
        assert set(views.TONES.values()) == {"neutral", "success", "caution", "failure"}

    def test_building_and_deploying_are_neutral(self):
        assert views.DEPLOYMENT_MARKS[S.BUILDING].tone == "neutral"
        assert views.DEPLOYMENT_MARKS[S.DEPLOYING].tone == "neutral"

    def test_a_timed_out_job_needs_attention_rather_than_failing(self):
        assert views.JOB_MARKS[JobStatus.TIMED_OUT].tone == "caution"
        assert views.JOB_MARKS[JobStatus.FAILED].tone == "failure"


class TestLine:
    def project(self, **kwargs):
        return fakes.project(**kwargs)

    def states(self, nodes):
        return [(n.key, n.state) for n in nodes]

    def test_building(self):
        nodes = views.line(dep(20, S.BUILDING), self.project(), is_current=False)
        assert self.states(nodes) == [
            ("source", "complete"),
            ("build", "active"),
            ("deploy", "pending"),
            ("production", "pending"),
        ]

    def test_current_production_ends_with_the_marker_and_no_time(self):
        nodes = views.line(dep(19), self.project(), is_current=True)
        assert self.states(nodes)[-1] == ("production", "current")
        assert nodes[-1].detail == ""

    def test_health_is_drawn_inside_deploy(self):
        nodes = views.line(dep(19), self.project(), is_current=True)
        assert [n.key for n in nodes] == ["source", "build", "deploy", "production"]
        assert nodes[2].extra == "includes health check"

    def test_a_preview_ends_at_deploy_and_never_reaches_production(self):
        nodes = views.line(dep(21, ref="feature/x"), self.project(), is_current=False)
        assert [n.key for n in nodes][-1] == "preview"
        assert "production" not in [n.key for n in nodes]

    def test_a_failed_build_breaks_the_line_at_build(self):
        d = dep(22, S.FAILED, built_at=None, ready_at=None, error="npm ERR!")
        assert self.states(views.line(d, self.project(), is_current=False)) == [
            ("source", "complete"),
            ("build", "failed"),
            ("deploy", "pending"),
            ("production", "pending"),
        ]

    def test_a_deployment_abandoned_in_the_queue_fails_at_source(self):
        d = dep(23, S.FAILED, started_at=None, built_at=None, ready_at=None)
        assert views.failed_step(d) == "source"
        assert views.line(d, self.project(), is_current=False)[0].state == "failed"

    def test_a_cancelled_deployment_ends_at_source(self):
        d = dep(24, S.CANCELLED, started_at=None, built_at=None, ready_at=None)
        assert self.states(views.line(d, self.project(), is_current=False)) == [
            ("source", "cancelled")
        ]

    def test_every_node_states_itself_in_words(self):
        for node in views.line(dep(19), self.project(), is_current=True):
            assert node.spoken.startswith(node.label + ": ")


class TestDetailActions:
    """Phase 3 §5.1: one contextually correct primary action per state."""

    def primary(self, d, project, production=None):
        return views.detail_actions(d, project, production).primary

    def test_queued_offers_cancel(self):
        assert self.primary(dep(20, S.QUEUED), fakes.project()).label == "Cancel"

    def test_building_and_deploying_offer_nothing(self):
        assert self.primary(dep(20, S.BUILDING), fakes.project()) is None
        assert self.primary(dep(20, S.DEPLOYING), fakes.project()) is None

    def test_failed_and_cancelled_offer_redeploy(self):
        assert self.primary(dep(20, S.FAILED), fakes.project()).label == "Redeploy"
        assert self.primary(dep(20, S.CANCELLED), fakes.project()).label == "Redeploy"

    def test_current_production_offers_redeploy_never_rollback(self):
        d = dep(19)
        project = fakes.project(production_deployment_id=d.id)
        actions = views.detail_actions(d, project, d)
        assert actions.primary.label == "Redeploy"
        assert all("Roll back" not in a.label for a in actions.secondary)

    def test_an_older_ready_deployment_names_itself_as_the_rollback_target(self):
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        older = dep(18, container_id=None)
        primary = self.primary(older, project, production)
        assert primary.label == "Roll back to #18"
        assert primary.note == "restarts in a few seconds"

    def test_a_newer_ready_deployment_offers_make_production(self):
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        primary = self.primary(dep(20), project, production)
        assert primary.label == "Make production"
        assert primary.note == "instant"

    def test_the_address_shows_only_while_it_answers(self):
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        stopped = dep(18, container_id=None)
        assert views.detail_actions(stopped, project, production).show_address is False
        assert views.detail_actions(production, project, production).show_address


class TestProductionBlock:
    """Phase 3 §3: the Overview's production block and its primary action."""

    def test_never_deployed_offers_deploy_with_the_full_branch_name(self):
        project = fakes.project(production_branch="claude/adoring-mayer-yfghmg")
        block = views.production_block(project, None, [])
        assert block.state == "empty"
        assert block.primary.label == "Deploy claude/adoring-mayer-yfghmg"

    def test_a_first_deploy_running_offers_view_deployment(self):
        running = dep(1, S.BUILDING)
        block = views.production_block(fakes.project(), None, [running])
        assert block.state == "first-running"
        assert block.primary.label == "View deployment"
        assert block.primary.method == "get"

    def test_deploy_is_never_silently_disabled_while_one_builds(self):
        """Q-S1: the button stays, with a note."""
        running = dep(1, S.BUILDING)
        block = views.production_block(fakes.project(), None, [running])
        deploy = block.secondary[0]
        assert deploy.note == (
            "#1 is already building — this will wait and build again after it."
        )

    def test_production_missing_offers_rollback_when_there_is_a_candidate(self):
        production = dep(19, container_id=None)
        older = dep(18)
        project = fakes.project(production_deployment_id=production.id)
        block = views.production_block(project, production, [production, older])
        assert block.state == "missing"
        assert block.primary.label == "Roll back to #18"

    def test_production_missing_without_a_candidate_offers_redeploy(self):
        production = dep(19, container_id=None)
        project = fakes.project(production_deployment_id=production.id)
        block = views.production_block(project, production, [production])
        assert block.primary.label == "Redeploy #19"

    def test_a_newer_deploy_in_progress_makes_view_it_primary(self):
        production = dep(19)
        newer = dep(20, S.BUILDING)
        project = fakes.project(production_deployment_id=production.id)
        block = views.production_block(project, production, [newer, production])
        assert block.primary.label == "View #20"
        assert block.secondary[0].label == "Deploy main"

    def test_a_preview_building_does_not_count_as_a_newer_production(self):
        production = dep(19)
        preview = dep(20, S.BUILDING, ref="feature/x")
        project = fakes.project(production_deployment_id=production.id)
        block = views.production_block(project, production, [preview, production])
        assert block.primary.label == "Deploy main"

    def test_rolled_back_names_the_deployment_it_came_from(self):
        newer = dep(21)
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        block = views.production_block(project, production, [newer, production])
        assert block.rolled_back_from.number == 21

    def test_rollback_targets_the_newest_older_ready_production_deployment(self):
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        deployments = [
            production,
            dep(18, ref="feature/x"),
            dep(17, S.FAILED),
            dep(16, container_id=None),
            dep(15),
        ]
        target, speed = views.rollback_target(project, production, deployments)
        assert target.number == 16
        assert speed == "restarts in a few seconds"

    def test_no_candidate_means_no_rollback_control(self):
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        assert views.rollback_target(project, production, [production]) is None


class TestNeedsAttention:
    """Phase 3 §6: derived from four recorded conditions, never stored."""

    def test_a_healthy_project(self):
        production = dep(19)
        project = fakes.project(production_deployment_id=production.id)
        found = views.conditions(project, production, [production], [])
        assert found == []
        assert views.project_mark(project, found).word == "Healthy"

    def test_production_missing_is_red_here_and_amber_on_the_project(self):
        """Phase 4 §5 mapping 2: contextual, not one colour per condition."""
        production = dep(19, container_id=None)
        project = fakes.project(production_deployment_id=production.id)
        found = views.conditions(project, production, [production], [])
        assert found[0].mark.tone == "failure"
        assert views.project_mark(project, found).tone == "caution"

    def test_the_most_serious_condition_comes_first(self):
        production = dep(19, container_id=None)
        project = fakes.project(production_deployment_id=production.id)
        failed_run = fakes.job_run(status=JobStatus.FAILED)
        found = views.conditions(
            project, production, [production], [(fakes.cron(), failed_run)]
        )
        assert found[0].text.startswith("Production is down")
        assert found[1].text == "nightly failed at 14:30."

    def test_a_failed_newer_deploy_while_production_is_fine(self):
        production = dep(12)
        project = fakes.project(production_deployment_id=production.id)
        found = views.conditions(project, production, [dep(13, S.FAILED), production], [])
        assert [c.text for c in found] == ["#13 failed. Production is still #12."]

    def test_never_deployed_is_its_own_state(self):
        project = fakes.project()
        assert views.project_mark(project, []).word == "Not deployed"


class TestConfiguration:
    def test_a_variable_changed_after_production_was_built(self):
        """Q-S3."""
        production = dep(19)
        after = production.started_at + timedelta(minutes=1)
        unchanged = fakes.env_var("API_KEY")
        changed = replace(fakes.env_var("NEW", EnvTarget.ALL), updated_at=after)
        preview_only = replace(
            fakes.env_var("PREVIEW", EnvTarget.PREVIEW), updated_at=after
        )
        found = views.changed_since_production(
            [unchanged, changed, preview_only], production
        )
        assert [v.key for v in found] == ["NEW"]

    def test_nothing_is_pending_before_anything_is_in_production(self):
        assert views.changed_since_production([fakes.env_var()], None) == []

    def test_domain_states(self):
        waiting = fakes.domain("www.example.com", verified=False, primary=False)
        alias = fakes.domain("www.example.com", verified=True, primary=False)
        primary = fakes.domain("example.com")
        assert (
            views.domain_mark(waiting, has_production=True, primary_host=None).word
            == "Waiting for DNS"
        )
        assert views.domain_mark(
            alias, has_production=True, primary_host="example.com"
        ).word == ("Verified, redirects to example.com")
        assert (
            views.domain_mark(primary, has_production=False, primary_host=None).word
            == "Verified, not yet serving"
        )

    def test_a_paused_worker_says_it_takes_effect_at_the_next_deploy(self):
        """Q-S4: the dashboard only flips the definition."""
        mark, sentence = views.worker_state(fakes.process(enabled=False), dep(19))
        assert mark.word == "Paused"
        assert "next deploy" in sentence

    def test_a_worker_never_claims_to_be_running(self):
        """Running counts need a Docker read ([small]); until then, none."""
        mark, sentence = views.worker_state(fakes.process(), dep(19))
        assert mark is None
        assert sentence == "Follows production #19."


class TestLabels:
    def test_a_long_branch_is_shortened_in_the_middle(self):
        head, tail = views.split_label("claude/adoring-mayer-yfghmg")
        assert head + tail == "claude/adoring-mayer-yfghmg"
        assert tail == "mayer-yfghmg"

    def test_a_short_branch_is_left_whole(self):
        assert views.split_label("main") == ("main", "")


class TestActivity:
    def test_only_what_happened_newest_first_and_at_most_ten(self):
        project = fakes.project()
        deployments = [(project, dep(n)) for n in range(1, 15)]
        deployments.append((project, dep(99, ref="feature/x")))
        events = views.activity(deployments, [])
        assert len(events) == 10
        assert all("went live" in e.text for e in events)

    def test_a_failure_says_where_it_failed(self):
        project = fakes.project()
        failed = dep(8, S.FAILED, built_at=None, ready_at=None)
        (event,) = views.activity([(project, failed)], [])
        assert event.text == "#8 failed at Building"
        assert event.mark.tone == "failure"

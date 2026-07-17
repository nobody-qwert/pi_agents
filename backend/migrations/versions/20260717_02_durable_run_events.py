"""Add transactional allocation and safe detail fields to durable run events.

Revision ID: 20260717_02
Revises: 20260717_01
Create Date: 2026-07-17 00:00:01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260717_02"
down_revision = "20260717_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column(
            "next_event_sequence",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.create_check_constraint(
        "runs_next_event_sequence_positive",
        "runs",
        "next_event_sequence >= 1",
    )
    op.add_column(
        "run_events",
        sa.Column("command_idempotency_key", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("transition_id", sa.String(length=128), nullable=True),
    )
    # These remain nullable at the storage layer only so this migration can be
    # applied to a database containing pre-packet audit rows.  EventDraft and
    # EventEnvelope require every value for all new writes.
    op.add_column(
        "run_events",
        sa.Column("attempt_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("design_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("packet_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("actor_role", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("outcome", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("correlation_id", sa.String(length=256), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("trace_id", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column("span_id", sa.String(length=16), nullable=True),
    )
    op.add_column(
        "run_events",
        sa.Column(
            "inline_detail",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "run_events",
        sa.Column("detail_ref", sa.String(length=384), nullable=True),
    )
    # Version 01 stored the old, smaller EventEnvelope only in ``payload``.
    # Normalize those rows before strict replay starts requiring this packet's
    # audit envelope fields.  Synthetic values are explicitly marked legacy;
    # raw old detail references are retained only when they already name this
    # event's safe, authorized detail endpoint.
    op.execute(
        """
        WITH legacy AS (
            SELECT
                event_id,
                run_id,
                CASE
                    WHEN payload ->> 'attempt_id'
                        ~ '^attempt_[A-Za-z0-9][A-Za-z0-9_-]{0,119}$'
                    THEN payload ->> 'attempt_id'
                    ELSE 'attempt_legacy_' || substring(event_id from 5 for 113)
                END AS attempt_id,
                CASE
                    WHEN payload ->> 'design_version' ~ '^[1-9][0-9]{0,8}$'
                    THEN (payload ->> 'design_version')::integer
                    ELSE 1
                END AS design_version,
                CASE
                    WHEN payload ->> 'packet_version' ~ '^[1-9][0-9]{0,8}$'
                    THEN (payload ->> 'packet_version')::integer
                    ELSE 1
                END AS packet_version,
                CASE
                    WHEN payload ->> 'status' IN (
                        'created', 'started', 'running', 'completed', 'failed',
                        'paused', 'blocked', 'accepted', 'rejected', 'ready', 'verified'
                    )
                    THEN payload ->> 'status'
                    ELSE 'created'
                END AS outcome,
                'legacy-event:' || event_id AS correlation_id,
                CASE
                    WHEN payload ->> 'trace_id' ~ '^[0-9a-f]{32}$'
                    THEN payload ->> 'trace_id'
                    ELSE NULL
                END AS trace_id,
                CASE
                    WHEN payload ->> 'span_id' ~ '^[0-9a-f]{16}$'
                    THEN payload ->> 'span_id'
                    ELSE NULL
                END AS span_id,
                CASE
                    WHEN run_id ~ '^run_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
                    AND event_id ~ '^evt_[A-Za-z0-9][A-Za-z0-9_-]{0,127}$'
                    AND payload ->> 'detail_ref'
                        = '/api/v1/runs/' || run_id || '/events/' || event_id || '/detail'
                    THEN payload ->> 'detail_ref'
                    ELSE NULL
                END AS detail_ref
            FROM run_events
        )
        UPDATE run_events AS event
        SET
            attempt_id = legacy.attempt_id,
            design_version = legacy.design_version,
            packet_version = legacy.packet_version,
            actor_role = 'legacy-event-log',
            outcome = legacy.outcome,
            correlation_id = legacy.correlation_id,
            trace_id = legacy.trace_id,
            span_id = legacy.span_id,
            detail_ref = legacy.detail_ref,
            payload = event.payload || jsonb_build_object(
                'attempt_id', legacy.attempt_id,
                'design_version', legacy.design_version,
                'packet_version', legacy.packet_version,
                'actor_role', 'legacy-event-log',
                'outcome', legacy.outcome,
                'correlation_id', legacy.correlation_id,
                'detail_ref', legacy.detail_ref
            )
        FROM legacy
        WHERE event.event_id = legacy.event_id
        """
    )
    # A legacy run may already have durable sequences.  Start allocation after
    # its high-water mark and update the authoritative payload in lockstep with
    # the audited record-version trigger.
    op.execute(
        """
        WITH stamp AS (SELECT clock_timestamp() AS value),
        high_water_marks AS (
            SELECT run_id, MAX(sequence) + 1 AS next_event_sequence
            FROM run_events
            GROUP BY run_id
        )
        UPDATE runs AS run
        SET
            next_event_sequence = high_water_marks.next_event_sequence,
            record_version = run.record_version + 1,
            updated_at = stamp.value,
            payload = jsonb_set(
                run.payload,
                '{metadata}',
                COALESCE(run.payload -> 'metadata', '{}'::jsonb)
                    || jsonb_build_object(
                        'record_version', run.record_version + 1,
                        'updated_at', stamp.value
                    ),
                true
            )
        FROM high_water_marks
        CROSS JOIN stamp
        WHERE run.run_id = high_water_marks.run_id
          AND run.next_event_sequence IS DISTINCT FROM high_water_marks.next_event_sequence
        """
    )
    op.create_unique_constraint(
        "uq_transition_log_run_transition",
        "transition_log",
        ["run_id", "transition_id"],
    )
    op.create_foreign_key(
        "fk_run_events_transition_owner",
        "run_events",
        "transition_log",
        ["run_id", "transition_id"],
        ["run_id", "transition_id"],
    )
    op.create_check_constraint(
        "run_events_detail_ref_safe",
        "run_events",
        "CASE WHEN detail_ref IS NULL THEN "
        "payload -> 'detail_ref' IS NULL OR payload -> 'detail_ref' = 'null'::jsonb "
        "ELSE detail_ref ~ "
        "'^/api/v1/runs/run_[A-Za-z0-9][A-Za-z0-9_-]{0,127}/events/"
        "evt_[A-Za-z0-9][A-Za-z0-9_-]{0,127}/detail$' "
        "AND detail_ref = '/api/v1/runs/' || run_id || '/events/' || event_id || '/detail' "
        "AND COALESCE(payload -> 'detail_ref' = to_jsonb(detail_ref), FALSE) END",
    )
    op.create_unique_constraint(
        "uq_run_events_command_idempotency",
        "run_events",
        ["run_id", "command_idempotency_key"],
    )
    # A command is reserved before its authoritative state change.  The unique
    # key makes duplicate transactions wait for the first transaction, without
    # serializing unrelated commands for the same run.
    op.create_table(
        "run_event_commands",
        sa.Column("run_id", sa.String(length=128), nullable=False),
        sa.Column("command_idempotency_key", sa.String(length=256), nullable=False),
        # The reservation deliberately precedes the state change.  Deferring
        # this check lets a state change create its run in the same
        # transaction, while PostgreSQL still rejects a committed reservation
        # whose run does not exist.
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["runs.run_id"],
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint(
            "run_id",
            "command_idempotency_key",
            name="pk_run_event_commands",
        ),
    )
    # Existing event rows were created before command reservations existed.
    # Backfilling keeps retries of those commands from repeating state changes.
    op.execute(
        "INSERT INTO run_event_commands (run_id, command_idempotency_key) "
        "SELECT run_id, command_idempotency_key FROM run_events "
        "WHERE command_idempotency_key IS NOT NULL"
    )
    op.create_index(
        "ix_run_events_replay",
        "run_events",
        ["run_id", "sequence"],
    )
    op.create_index(
        "ix_run_events_correlation",
        "run_events",
        ["run_id", "correlation_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_run_events_correlation", table_name="run_events")
    op.drop_index("ix_run_events_replay", table_name="run_events")
    op.drop_table("run_event_commands")
    op.drop_constraint(
        "uq_run_events_command_idempotency", "run_events", type_="unique"
    )
    op.drop_constraint("run_events_detail_ref_safe", "run_events", type_="check")
    op.drop_constraint(
        "fk_run_events_transition_owner", "run_events", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_transition_log_run_transition", "transition_log", type_="unique"
    )
    op.drop_column("run_events", "detail_ref")
    op.drop_column("run_events", "inline_detail")
    op.drop_column("run_events", "transition_id")
    op.drop_column("run_events", "span_id")
    op.drop_column("run_events", "trace_id")
    op.drop_column("run_events", "correlation_id")
    op.drop_column("run_events", "outcome")
    op.drop_column("run_events", "actor_role")
    op.drop_column("run_events", "packet_version")
    op.drop_column("run_events", "design_version")
    op.drop_column("run_events", "attempt_id")
    op.drop_column("run_events", "command_idempotency_key")
    op.drop_constraint("runs_next_event_sequence_positive", "runs", type_="check")
    op.drop_column("runs", "next_event_sequence")

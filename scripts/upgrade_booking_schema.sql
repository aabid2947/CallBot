-- VoiceStream booking_requests — Feature 4 schema upgrade (idempotent).
--
-- A FRESH database gets these columns automatically from SQLAlchemy
-- (Base.metadata.create_all in core/booking/db.py via init_db()). Run this
-- ONLY when the booking_requests table ALREADY exists in the shared Supabase
-- Postgres and needs the new Feature-4 columns added. Safe to run repeatedly.

alter table booking_requests
    add column if not exists appointment_type    varchar(20) not null default 'medical',
    add column if not exists target_phone        varchar(50),
    add column if not exists scheduled_call_at    timestamptz,
    add column if not exists caller_user_id       varchar(64),
    add column if not exists aiva_chat_id          varchar(64),
    add column if not exists contact_info          text,
    add column if not exists call_triggered_at     timestamptz,
    add column if not exists outcome_notified_at   timestamptz;

-- date_of_birth is now optional (required only for 'medical', enforced in app code).
alter table booking_requests
    alter column date_of_birth drop not null;

create index if not exists booking_requests_caller_user_id_idx
    on booking_requests (caller_user_id);

-- Normalize legacy status values to the lowercase enum VALUE.
-- SQLAlchemy previously stored the member NAME ('PENDING','CONFIRMED',…); the model
-- now uses values_callable so it stores/queries the value ('pending','confirmed',…),
-- matching what AIVA writes. Run this AFTER deploying the updated models.py, or
-- VoiceStream's name-based reads of the lowercased rows will fail.
update booking_requests set status = lower(status);

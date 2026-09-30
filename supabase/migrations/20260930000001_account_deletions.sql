-- Migration 20260930000001: user-account soft-delete ledger (#4029)
--
-- Backs the self-service account-deletion flow. Deleting a personal account is
-- two-phase, mirroring the team path (#302): the request stamps `deleted_at` +
-- the grace window PROMISED at schedule time, and the boot + hourly purge
-- erases the account (GoTrue auth-user delete) once the STORED window elapses.
--
-- Why a ledger table rather than a column on an existing row: an account can
-- own zero orgs, so there is no `org_memberships`/`organizations` row to stamp.
-- `user_id` is the primary key, so a concurrent schedule can only land ONE row
-- (INSERT ... ON CONFLICT is not needed — the API read-then-inserts and the PK
-- turns a lost race into a 409 the caller treats as already-scheduled).
--
-- Deliberately NO foreign key to `auth.users`: this row is the purge's RETRY
-- ANCHOR. The purge deletes the auth user FIRST and this row LAST, so a partial
-- failure leaves the row in place for the next sweep; an ON DELETE CASCADE from
-- auth.users would silently drop the anchor the moment the auth user is erased.
--
-- Additive only. Service-role reads/writes only (RLS deny-by-default); the
-- window itself is never stated here — the sole authority is
-- `tortoise/retention.py` (see docs/retention-and-deletion.md).

CREATE TABLE IF NOT EXISTS public.account_deletions (
    user_id     uuid PRIMARY KEY,
    deleted_at  timestamptz NOT NULL,
    grace_hours numeric NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.account_deletions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.account_deletions FROM anon, authenticated, public;
GRANT ALL ON public.account_deletions TO service_role;

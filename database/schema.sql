-- ============================================================
-- Hyderabad Event Radar — Supabase Schema
-- Run once in Supabase SQL Editor.
-- The service-role key bypasses RLS; never use it in client-side code.
-- ============================================================

-- ============================================================
-- 1. Events table
-- ============================================================
create table if not exists public.events (
  id                       bigint generated always as identity primary key,
  event_id                 text        not null unique,
  source                   text        not null,
  title                    text        not null,
  url                      text        not null,
  event_date               timestamptz,
  location                 text,
  price                    numeric,
  is_free                  boolean     not null default false,
  description              text,
  first_seen               timestamptz not null default now(),
  last_seen                timestamptz not null default now(),
  notified                 boolean     not null default false,
  notified_at              timestamptz,
  notification_claimed_at  timestamptz,
  notification_attempts    integer     not null default 0,
  notification_error       text,
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create index if not exists events_source_idx      on public.events (source);
create index if not exists events_event_date_idx  on public.events (event_date);
create index if not exists events_first_seen_idx  on public.events (first_seen);
create index if not exists events_is_free_idx     on public.events (is_free) where is_free;
create index if not exists events_notified_idx    on public.events (notified) where not notified;

-- ============================================================
-- 2. Source health table  (plan §20)
-- ============================================================
create table if not exists public.source_status (
  id               bigint generated always as identity primary key,
  source           text        not null unique,
  last_run         timestamptz,
  last_success     timestamptz,
  events_found     integer     not null default 0,
  consecutive_failures integer not null default 0,
  status           text        not null default 'UNKNOWN',   -- SUCCESS | ERROR | UNKNOWN
  last_error       text,
  updated_at       timestamptz not null default now()
);

-- ============================================================
-- 3. Triggers
-- ============================================================
create or replace function public.set_event_timestamps()
returns trigger language plpgsql as $$
begin
  new.last_seen  = now();
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists set_event_timestamps on public.events;
create trigger set_event_timestamps
before update on public.events
for each row execute function public.set_event_timestamps();

-- ============================================================
-- 4. Claim pending free events (atomic, skip-locked)
-- ============================================================
-- Claims expire after 15 minutes so a crashed worker never blocks alerts.
-- FOR UPDATE SKIP LOCKED prevents overlapping /run calls from double-claiming.
create or replace function public.claim_pending_free_events(claim_limit integer default 25)
returns setof public.events
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with candidates as (
    select id
    from   public.events
    where  is_free = true
      and  notified = false
      and  (notification_claimed_at is null
            or notification_claimed_at < now() - interval '15 minutes')
    order by first_seen asc
    limit  claim_limit
    for update skip locked
  ), claimed as (
    update public.events e
    set    notification_claimed_at = now(),
           updated_at              = now()
    from   candidates c
    where  e.id = c.id
    returning e.*
  )
  select * from claimed;
end;
$$;

-- ============================================================
-- 5. Release a failed notification claim
-- ============================================================
create or replace function public.release_notification_claim(
  target_event_id text,
  failure_message text
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.events
  set    notification_claimed_at = null,
         notification_attempts   = notification_attempts + 1,
         notification_error      = failure_message,
         updated_at              = now()
  where  event_id = target_event_id
    and  notified = false;
end;
$$;

-- ============================================================
-- 6. Upsert source status  (called by the Python app after each run)
-- ============================================================
create or replace function public.upsert_source_status(
  p_source               text,
  p_status               text,
  p_events_found         integer,
  p_error                text default null
)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  insert into public.source_status (source, last_run, status, events_found, last_error,
                                    consecutive_failures, updated_at)
  values (p_source, now(), p_status, p_events_found,
          p_error,
          case when p_status = 'ERROR' then 1 else 0 end,
          now())
  on conflict (source) do update
    set last_run             = now(),
        status               = p_status,
        events_found         = p_events_found,
        last_error           = p_error,
        last_success         = case when p_status = 'SUCCESS'
                                    then now()
                                    else source_status.last_success end,
        consecutive_failures = case when p_status = 'ERROR'
                                    then source_status.consecutive_failures + 1
                                    else 0 end,
        updated_at           = now();
end;
$$;

-- ============================================================
-- 7. 90-day cleanup  (schedule weekly or monthly in Supabase Cron)
-- ============================================================
-- delete from public.events
-- where first_seen < now() - interval '90 days';

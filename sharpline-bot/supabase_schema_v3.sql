-- SharpLine schema v3 — anchor components + Kelly-based record.
-- Run in Supabase SQL editor (safe to re-run).

alter table public.alerts add column if not exists anchor_probs jsonb;

create or replace view public.site_record as
select
  coalesce(book_category, 'all')                        as scope,
  count(*) filter (where result = 'win')                as wins,
  count(*) filter (where result = 'loss')               as losses,
  count(*) filter (where result = 'push')               as pushes,
  count(*) filter (where result is null
                   and commence > now())                as pending,
  round(sum(case when result = 'win'  then odds - 1
                 when result = 'loss' then -1
                 else 0 end)::numeric, 2)               as units,
  round((sum(case when result = 'win'  then odds - 1
                  when result = 'loss' then -1
                  else 0 end)
         / nullif(count(*) filter (where result in ('win','loss')), 0)
         * 100)::numeric, 1)                            as roi_pct,
  round(sum(case when result = 'win'
                   then coalesce(stake_units, 1) * (odds - 1)
                 when result = 'loss'
                   then -coalesce(stake_units, 1)
                 else 0 end)::numeric, 2)               as kelly_units,
  round((sum(case when result = 'win'
                    then coalesce(stake_units, 1) * (odds - 1)
                  when result = 'loss'
                    then -coalesce(stake_units, 1)
                  else 0 end)
         / nullif(sum(coalesce(stake_units, 1))
                  filter (where result in ('win','loss')), 0)
         * 100)::numeric, 1)                            as kelly_roi_pct,
  round(avg(clv_pct)::numeric, 2)                       as avg_clv_pct,
  round((count(*) filter (where clv_pct > 0)::numeric
         / nullif(count(*) filter (where clv_pct is not null), 0)
         * 100), 1)                                     as beat_close_pct
from public.alerts
group by grouping sets ((book_category), ());

create or replace view public.site_record_by_book as
select
  book, book_category,
  count(*)                                              as picks,
  count(*) filter (where result = 'win')                as wins,
  count(*) filter (where result = 'loss')               as losses,
  count(*) filter (where result = 'push')               as pushes,
  round(sum(case when result = 'win'  then odds - 1
                 when result = 'loss' then -1
                 else 0 end)::numeric, 2)               as units,
  round((sum(case when result = 'win'  then odds - 1
                  when result = 'loss' then -1
                  else 0 end)
         / nullif(count(*) filter (where result in ('win','loss')), 0)
         * 100)::numeric, 1)                            as roi_pct,
  round(sum(case when result = 'win'
                   then coalesce(stake_units, 1) * (odds - 1)
                 when result = 'loss'
                   then -coalesce(stake_units, 1)
                 else 0 end)::numeric, 2)               as kelly_units,
  round(avg(ev_pct)::numeric, 2)                        as avg_ev_pct,
  round(avg(clv_pct)::numeric, 2)                       as avg_clv_pct
from public.alerts
group by book, book_category
order by picks desc;

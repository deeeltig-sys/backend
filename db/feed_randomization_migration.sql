-- ============================================================
-- FEED RANDOMIZATION — the feed view previously ordered strictly by
-- score desc, so the same high-engagement posts always sat at the
-- top and everything else was permanently buried below them, every
-- single time the feed loaded. This replaces that with weighted
-- random sampling (the standard "A-algorithm" approach): every post
-- still gets a fair shot at the top, but higher-scoring posts remain
-- statistically more likely to surface first. Nothing is ever stuck
-- at a fixed position anymore — reload the feed and the order shifts.
-- ============================================================

create or replace view feed as
select
  p.*,
  feed_score(p.view_count, p.reaction_count, p.search_hit_count) as score
from posts p
where p.status = 'active'
order by power(random(), 1.0 / (feed_score(p.view_count, p.reaction_count, p.search_hit_count) + 1)) desc;

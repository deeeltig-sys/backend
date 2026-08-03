-- typing_indicator_migration.sql
--
-- Adds a "X is typing…" indicator to conversations, in the same
-- polling style as the rest of the chat system (no websockets/
-- Realtime infra needed). One column + two RPCs:
--   set_typing(p_conversation_id, p_typing)   — caller marks themself
--     typing/not-typing in a conversation.
--   get_other_typing(p_conversation_id)       — is the OTHER
--     participant currently (within the last 6s) typing?
--
-- get_other_typing is SECURITY DEFINER because conv_user_state_own
-- (see chat_overhaul_migration.sql) only lets a user read their OWN
-- row — correct for hidden/deleted/wallpaper, but it means the normal
-- RLS path can't show you someone else's typing_at. The function
-- itself re-checks that the caller is actually a participant in the
-- conversation before returning anything, so this doesn't open up
-- reading anyone else's state, just this one boolean.

alter table conversation_user_state
  add column if not exists typing_at timestamptz;

create or replace function set_typing(p_conversation_id uuid, p_typing boolean)
returns void
language plpgsql
security invoker
as $$
begin
  insert into conversation_user_state (conversation_id, user_id, typing_at)
  values (p_conversation_id, auth.uid(), case when p_typing then now() else null end)
  on conflict (conversation_id, user_id)
  do update set typing_at = case when p_typing then now() else null end;
end;
$$;

create or replace function get_other_typing(p_conversation_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  is_participant boolean;
  other_typing_at timestamptz;
begin
  select exists(
    select 1 from conversations
    where id = p_conversation_id
      and (user_a = auth.uid() or user_b = auth.uid())
  ) into is_participant;

  if not is_participant then
    return false;
  end if;

  select typing_at into other_typing_at
  from conversation_user_state
  where conversation_id = p_conversation_id
    and user_id <> auth.uid()
  limit 1;

  return other_typing_at is not null and other_typing_at > now() - interval '6 seconds';
end;
$$;

grant execute on function set_typing(uuid, boolean) to authenticated;
grant execute on function get_other_typing(uuid) to authenticated;

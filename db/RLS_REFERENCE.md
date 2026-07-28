# RLS Policy Reference — Source of Truth

This is a **read-only reference doc**, not a migration to run. It exists so
every RLS policy in the project has one place to look, instead of hunting
across the files below. When a table's policies change, update the actual
migration file first, then reflect the change here.

Source files this was compiled from:
- `rls_policies.sql` (duplicated verbatim inside `schema.sql`)
- `friends_migration.sql`
- `v3_social_migration.sql`
- `chat_overhaul_migration.sql`
- `status_and_settings_migration.sql`
- `saved_posts_migration.sql`
- `storage_policies.sql`
- `avatar_storage_policies.sql`

---

## users
| Policy | Operation | Rule |
|---|---|---|
| `users_select_all` | SELECT | `true` — profiles are public |
| `users_update_own` | UPDATE | `id = auth.uid()` |
| `users_staff_update` | UPDATE | `is_staff()` |

## posts
| Policy | Operation | Rule |
|---|---|---|
| `posts_select` | SELECT | `status = 'active' OR author_id = auth.uid() OR is_staff()` |
| `posts_insert` | INSERT | `author_id = auth.uid()` |
| `posts_update_own` | UPDATE | `author_id = auth.uid() OR is_staff()` — self-delete is soft (status → removed) |
| `posts_delete_staff` | DELETE | `is_staff()` — hard delete is staff-only |

## reactions
| Policy | Operation | Rule |
|---|---|---|
| `reactions_select` | SELECT | `true` |
| `reactions_insert` | INSERT | `user_id = auth.uid()` |
| `reactions_delete_own` | DELETE | `user_id = auth.uid()` |

## comments
| Policy | Operation | Rule |
|---|---|---|
| `comments_select` | SELECT | `status = 'active' OR author_id = auth.uid() OR is_staff()` |
| `comments_insert` | INSERT | `author_id = auth.uid()` |
| `comments_update_own` | UPDATE | `author_id = auth.uid() OR is_staff()` |
| `comments_delete_own` | DELETE | `author_id = auth.uid() OR is_staff()` |

## reports
| Policy | Operation | Rule |
|---|---|---|
| `reports_insert` | INSERT | `reporter_id = auth.uid()` |
| `reports_select_own_staff` | SELECT | `reporter_id = auth.uid() OR is_staff()` |
| `reports_staff_update` | UPDATE | `is_staff()` |

## hidden_posts
| Policy | Operation | Rule |
|---|---|---|
| `hidden_posts_all_own` | ALL | `user_id = auth.uid()` (using + with check) |

## blocks
| Policy | Operation | Rule |
|---|---|---|
| `blocks_all_own` | ALL | `blocker_id = auth.uid()` (using + with check) |

## friend_requests
| Policy | Operation | Rule |
|---|---|---|
| `friend_requests_select` | SELECT | `sender_id = auth.uid() OR receiver_id = auth.uid()` |
| `friend_requests_insert` | INSERT | `sender_id = auth.uid()` |
| `friend_requests_update_receiver` | UPDATE | `receiver_id = auth.uid()` — only the receiver can accept/decline |
| `friend_requests_delete_sender` | DELETE | `sender_id = auth.uid() AND status = 'pending'` — sender can cancel a still-pending request |

## friendships
| Policy | Operation | Rule |
|---|---|---|
| `friendships_select` | SELECT | `user_a = auth.uid() OR user_b = auth.uid()` |
| *(no insert/update/delete policy — intentional)* | — | All writes go through `send_friend_request()`, `respond_to_friend_request()`, `remove_friendship()` (all `SECURITY DEFINER`) to enforce the `user_a < user_b` canonical ordering. Not a gap. |

## follows
| Policy | Operation | Rule |
|---|---|---|
| `follows_select_all` | SELECT | `true` |
| `follows_insert_own` | INSERT | `follower_id = auth.uid()` |
| `follows_delete_own` | DELETE | `follower_id = auth.uid()` |

## notifications
| Policy | Operation | Rule |
|---|---|---|
| `notifications_select_own` | SELECT | `user_id = auth.uid()` |
| `notifications_update_own` | UPDATE | `user_id = auth.uid()` |
| *(no insert/delete policy — intentional)* | — | Rows are only created by `SECURITY DEFINER` triggers (`notify_on_follow`, `notify_on_comment`, `notify_on_reaction`, message notify trigger), so nobody can forge a notification as another user. |

## conversations
| Policy | Operation | Rule |
|---|---|---|
| `conversations_select_own` | SELECT | `auth.uid() IN (user_a, user_b)` |
| *(no direct insert/update policy)* | — | Created/accepted only via `start_conversation()` / `accept_conversation()` (`SECURITY DEFINER`), which enforce canonical `user_a < user_b` ordering and block checks. |

## messages
| Policy | Operation | Rule |
|---|---|---|
| `messages_select_own` | SELECT | Participant of the parent conversation |
| `messages_insert_own` | INSERT | `sender_id = auth.uid()` AND conversation is `accepted`, OR still `pending` but you're the one who started it — this is the message-request gate, enforced at the DB level |
| `messages_update_read_receipt` | UPDATE | `sender_id <> auth.uid()` AND you're a participant — lets a recipient mark `read_at`, but never on your own sent message |

## conversation_user_state
| Policy | Operation | Rule |
|---|---|---|
| `conv_user_state_own` | ALL | `user_id = auth.uid()` — hide/delete/clear/wallpaper are all private to each participant's own view of the chat |

## statuses
| Policy | Operation | Rule |
|---|---|---|
| `statuses_select_active` | SELECT | `expires_at > now()` — visible to any signed-in student, same audience as the main feed |
| `statuses_insert_own` | INSERT | `author_id = auth.uid()` |
| `statuses_delete_own` | DELETE | `author_id = auth.uid()` |

## status_views
| Policy | Operation | Rule |
|---|---|---|
| `status_views_insert_own` | INSERT | `viewer_id = auth.uid()` |
| `status_views_select` | SELECT | `viewer_id = auth.uid()` OR you authored the status being viewed (the "seen by" list) |

## saved_posts
| Policy | Operation | Rule |
|---|---|---|
| `saved_posts_own` | ALL | `user_id = auth.uid()` (using + with check) |

## storage.objects — bucket: post-images
| Policy | Operation | Rule |
|---|---|---|
| `post images are publicly readable` | SELECT | `bucket_id = 'post-images'` — public feed, public read |
| `students upload only into their own folder` | INSERT | authenticated AND first path segment = `auth.uid()` |
| `students delete only their own images` | DELETE | own folder, OR `role = 'admin'` for moderation cleanup |

## storage.objects — bucket: avatars
| Policy | Operation | Rule |
|---|---|---|
| `avatars are publicly readable` | SELECT | `bucket_id = 'avatars'` |
| `students upload only their own avatar` | INSERT | authenticated AND first path segment = `auth.uid()` |
| `students manage only their own avatar` | UPDATE | own folder — re-uploading replaces the previous file |
| `students delete only their own avatar` | DELETE | own folder |

---

## Known non-gaps (reviewed, not oversights)
- **friendships** has no client-facing write policy — enforced entirely through
  `SECURITY DEFINER` functions to keep the `user_a < user_b` invariant intact.
- **notifications** has no client-facing insert — only server-side triggers
  create them, preventing spoofed notifications.
- **conversations** has no client-facing insert/update — only
  `start_conversation()` / `accept_conversation()` can create or transition one.

## Operational note
These policies currently live across 8 separate files (list above) rather
than one canonical schema. That's a maintainability risk, not a live
vulnerability — but when adding a new table, cross-check this doc first so
RLS isn't accidentally skipped, then add both the real policy in the
relevant migration file **and** a row here.

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../data/order_repository.dart';
import '../models/order.dart';
import 'providers.dart';

/// A page-accumulating view of `GET /orders`.
///
/// Why this is a notifier and not a `FutureProvider`: the endpoint is
/// keyset-paginated and the list is append-only, so "load more" has to *add* to
/// what is already on screen. `FutureProvider` recomputes from scratch, which
/// would either lose the earlier pages or re-fetch them on every tap. The
/// read/write rule still holds — this changes nothing on the server, it only
/// accumulates reads. Anything that mutates an order still goes through a
/// repository call followed by `ref.invalidate`.
///
/// The cursor is the id of the last row already held. The server returns no
/// cursor of its own, so "there is more" is inferred from a full page — see
/// [loadMore] for the terminal cases. Repeated calls are ignored while a page is
/// in flight, so a fast scroll cannot fire two requests for the same cursor.
class OrderHistoryState {
  const OrderHistoryState({
    required this.items,
    required this.loadingMore,
    required this.exhausted,
    this.error,
  });

  const OrderHistoryState.initial()
    : items = const <Order>[],
      loadingMore = false,
      exhausted = false,
      error = null;

  final List<Order> items;

  /// A *subsequent* page is in flight. The first page is the notifier's own
  /// `AsyncValue.loading`, so this only ever drives the "load more" spinner.
  final bool loadingMore;

  /// The server stopped handing out rows — a short page or a non-advancing
  /// cursor. Distinct from "not loaded yet".
  final bool exhausted;

  /// A failed *subsequent* page. The first-page failure lives on the
  /// `AsyncValue`, which is what the screen's retry button reads.
  final Object? error;

  bool get hasMore => !exhausted;
}

/// Loads page 1, then one page per [loadMore].
///
/// Riverpod 3 dropped `FamilyAsyncNotifier`; a family's argument now reaches the
/// notifier through the factory that creates it, so the role is captured here
/// rather than read off `ref`.
class OrderHistoryController extends AsyncNotifier<OrderHistoryState> {
  OrderHistoryController(this._role);

  /// The order role this history is scoped to (`passenger`, `driver`, ...).
  final String _role;

  static const int _pageSize = 20;

  /// Bounded like `ledgerProvider`: a runaway non-advancing cursor must not
  /// spin forever.
  static const int _maxPages = 50;

  OrderRepository get _repo => ref.read(orderRepositoryProvider);

  @override
  Future<OrderHistoryState> build() async {
    final OrderPage first = await _repo.history(role: _role, limit: _pageSize);
    return OrderHistoryState(
      items: first.items,
      loadingMore: false,
      exhausted: first.nextCursor == null,
    );
  }

  /// Appends the next page. Safe to call from a scroll listener: it no-ops
  /// while a page is in flight or once the cursor is gone.
  Future<void> loadMore() async {
    final OrderHistoryState? current = state.value;
    if (current == null || current.loadingMore || current.exhausted) {
      return;
    }

    final List<Order> items = current.items;
    // `items.last.id` is the cursor; an empty list means there is nothing to
    // page from, and `nextCursor` would already have said exhausted.
    final String? cursor = items.isEmpty ? null : items.last.id;
    if (cursor == null) {
      state = AsyncData<OrderHistoryState>(_exhausted(current));
      return;
    }

    state = AsyncData<OrderHistoryState>(
      OrderHistoryState(items: items, loadingMore: true, exhausted: false),
    );

    try {
      final OrderPage page = await _repo.history(role: _role, limit: _pageSize, beforeId: cursor);

      // A page that returns nothing is the end, even if it looked full before:
      // treating it as "keep the cursor" would loop forever.
      final bool noProgress = page.items.isEmpty;
      if (noProgress) {
        state = AsyncData<OrderHistoryState>(
          OrderHistoryState(items: items, loadingMore: false, exhausted: true),
        );
        return;
      }

      final List<Order> merged = <Order>[...items, ...page.items];
      // The server sends no cursor, so the end is a short page — or a cursor
      // that did not advance, which is also terminal. Guarded on total pages so
      // a misbehaving server cannot grow this unboundedly.
      //
      // `page.nextCursor == merged.last.id` is *not* the non-advance test:
      // when a full page arrives, `nextCursor` IS `merged.last.id` by
      // construction (`models/order.dart`), so that comparison is true on every
      // successful full page and would mark the list exhausted after one page.
      // The real non-advance case is an empty page, already handled above;
      // a non-empty page always advances because `before_id` is `items.last.id`.
      final bool shortPage = page.items.length < _pageSize;
      final bool tooMany = merged.length > _pageSize * _maxPages;

      state = AsyncData<OrderHistoryState>(
        OrderHistoryState(items: merged, loadingMore: false, exhausted: shortPage || tooMany),
      );
    } catch (error) {
      // Keep what is on screen and surface the failure beside the list; the
      // user can retry the next page without losing the pages already read.
      state = AsyncData<OrderHistoryState>(
        OrderHistoryState(items: items, loadingMore: false, exhausted: false, error: error),
      );
    }
  }

  /// Re-reads page 1 and drops everything after it.
  Future<void> refresh() async {
    ref.invalidateSelf();
    await future;
  }

  static OrderHistoryState _exhausted(OrderHistoryState current) =>
      OrderHistoryState(items: current.items, loadingMore: false, exhausted: true);
}

/// Keyed by role, matching `orderHistoryProvider`'s argument so the two can
/// coexist while the old call sites are migrated.
final orderHistoryControllerProvider =
    AsyncNotifierProvider.family<OrderHistoryController, OrderHistoryState, String>(
      OrderHistoryController.new,
    );

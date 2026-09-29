import '../core/format/money.dart';
import '../core/network/wire.dart';
import 'enums.dart';

/// One statement line. `balance_after_hkd` is the running balance the server
/// computed when the entry was written, so the UI never has to re-derive it.
class LedgerEntry {
  const LedgerEntry({
    required this.id,
    required this.entryType,
    required this.amountHkd,
    required this.balanceAfterHkd,
    required this.orderId,
    required this.note,
    required this.createdAt,
  });

  factory LedgerEntry.fromJson(Map<String, dynamic> json) => LedgerEntry(
    id: asInt(json['id'], 'ledger.id'),
    entryType: LedgerEntryType.fromWire(asString(json['entry_type'], 'ledger.entry_type')),
    amountHkd: Money.parse(json['amount_hkd']),
    balanceAfterHkd: Money.parse(json['balance_after_hkd']),
    orderId: asStringOrNull(json['order_id'], 'ledger.order_id'),
    note: asStringOrNull(json['note'], 'ledger.note'),
    createdAt: asDateOrNull(json['created_at'], 'ledger.created_at'),
  );

  final int id;
  final LedgerEntryType entryType;
  final Money amountHkd;
  final Money balanceAfterHkd;
  final String? orderId;
  final String? note;
  final DateTime? createdAt;
}

/// `GET /api/v1/drivers/me/ledger` — ascending by id, keyset-paginated on the
/// integer `id`. `next_cursor` is non-null only while a full page came back, so
/// a null cursor means "end of statement" (`P1-8`).
class LedgerPage {
  const LedgerPage({required this.items, required this.nextCursor});

  factory LedgerPage.fromJson(Map<String, dynamic> json) => LedgerPage(
    items: asObjectList(json['items'], 'items', LedgerEntry.fromJson),
    nextCursor: asIntOrNull(json['next_cursor'], 'next_cursor'),
  );

  final List<LedgerEntry> items;
  final int? nextCursor;

  bool get hasMore => nextCursor != null;
}

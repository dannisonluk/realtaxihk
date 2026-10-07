import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/network/api_exception.dart';
import '../data/driver_repository.dart';
import '../models/admin.dart';
import '../models/auth.dart';
import '../models/driver.dart';
import '../models/driver_attributes.dart';
import '../models/driver_booking_preferences.dart';
import '../models/driver_notification.dart';
import '../models/enums.dart';
import '../models/fixed_offer.dart';
import '../models/fleet.dart';
import '../models/identity.dart';
import '../models/landmark.dart';
import '../models/ledger.dart';
import '../models/order.dart';
import '../models/recurring_ride.dart';
import '../models/refund.dart';
import 'providers.dart';

/// Read-only projections of the API, as providers.
///
/// Screens use these for anything they display; anything that *changes* state
/// goes through a repository call followed by `ref.invalidate(...)` on the
/// relevant provider, so there is exactly one code path that mutates the server
/// and one that reads it back.

/// The caller's own profile: the phone's proven state, and the P-4 re-verification
/// deadlines. Null while signed out.
///
/// Kept apart from [currentUserProvider], which holds the three fields a token
/// response carries. Only this one knows whether a number has been **proven**,
/// so it is the source the unlock gate and the account screen read.
///
/// It **watches the session**, so signing in or out rebuilds it: a cached profile
/// belongs to whoever was signed in when it was fetched, and a new account
/// showing the previous account's proven phone would be a real leak of state, not
/// just a stale render. Invalidate it by hand after a `/identity/phone/*` call
/// that the caller does not otherwise re-render from.
final FutureProvider<Profile?> profileProvider = FutureProvider<Profile?>((Ref ref) async {
  final AppUser? user = ref.watch(currentUserProvider);
  if (user == null) {
    return null;
  }
  return ref.watch(identityRepositoryProvider).me();
});

/// The caller's driver profile, or null when they have none.
///
/// `GET /drivers/me` answers 404 `NOT_FOUND` for an account that never
/// registered, which is a normal state rather than an error — this provider
/// turns it into `null` so the UI can branch on it.
final FutureProvider<DriverProfile?> driverProfileProvider = FutureProvider<DriverProfile?>((
  Ref ref,
) async {
  try {
    return await ref.watch(driverRepositoryProvider).me();
  } on ApiException catch (e) {
    if (e.code == ApiException.notFound) {
      return null;
    }
    rethrow;
  }
});

/// The full statement, following `next_cursor` until the server stops handing
/// one out. A driver's ledger grows without bound, so the API pages it (`P1-8`);
/// the screen only ever shows a screenful, but the balance history is short
/// enough that walking the cursor here keeps the UI simple.
final FutureProvider<List<LedgerEntry>> ledgerProvider = FutureProvider<List<LedgerEntry>>((
  Ref ref,
) async {
  final DriverRepository repo = ref.watch(driverRepositoryProvider);
  final List<LedgerEntry> all = <LedgerEntry>[];
  int? cursor;
  // Bounded: 200 rows a page, 20 pages is 4000 entries — far past anything a
  // driver scrolls, and it stops a runaway loop if the server ever returns a
  // non-advancing cursor.
  for (int page = 0; page < 20; page++) {
    final LedgerPage chunk = await repo.ledger(limit: 200, afterId: cursor);
    all.addAll(chunk.items);
    cursor = chunk.nextCursor;
    if (cursor == null) {
      break;
    }
  }
  return all.reversed.toList(growable: false);
});

/// The driver's most recent refund request, or null.
final FutureProvider<RefundRequest?> myRefundProvider = FutureProvider<RefundRequest?>(
  (Ref ref) => ref.watch(driverRepositoryProvider).myRefund(),
);

/// The driver's declared payment methods.
final FutureProvider<DriverPaymentMethods> driverPaymentMethodsProvider =
    FutureProvider<DriverPaymentMethods>(
      (Ref ref) => ref.watch(driverRepositoryProvider).paymentMethods(),
    );

/// The driver's in-car environment capability flags.
final FutureProvider<DriverEnvironment> driverEnvironmentProvider =
    FutureProvider<DriverEnvironment>(
      (Ref ref) => ref.watch(driverRepositoryProvider).environment(),
    );

/// The driver's standing pre-booking preferences.
final FutureProvider<DriverBookingPreferences> driverBookingPreferencesProvider =
    FutureProvider<DriverBookingPreferences>(
      (Ref ref) => ref.watch(orderRepositoryProvider).getDriverBookingPreferences(),
    );

/// The driver's notification inbox, newest first.
final FutureProvider<DriverNotificationPage> driverNotificationsProvider =
    FutureProvider<DriverNotificationPage>(
      (Ref ref) => ref.watch(driverNotificationRepositoryProvider).list(limit: 50),
    );

/// The driver's standing fixed-fare offers (一口價).
final FutureProvider<List<FixedOffer>> fixedOffersProvider = FutureProvider<List<FixedOffer>>(
  (Ref ref) async => (await ref.watch(driverRepositoryProvider).fixedOffers()).items,
);

/// The passenger's weekly ride templates.
final FutureProvider<List<RecurringRide>> recurringRidesProvider =
    FutureProvider<List<RecurringRide>>(
      (Ref ref) => ref.watch(recurringRideRepositoryProvider).list(),
    );

/// Active premium destinations shown as avatar pins on the map.
final FutureProvider<PremiumDestinationPage> premiumDestinationsProvider =
    FutureProvider<PremiumDestinationPage>(
      (Ref ref) => ref.watch(destinationRepositoryProvider).list(),
    );

/// Named dropoff landmarks, optionally filtered by category.
final landmarksProvider = FutureProvider.family<List<Landmark>, String?>(
  (Ref ref, String? category) =>
      ref.watch(orderRepositoryProvider).fetchLandmarks(category: category),
);

/// One order, by id.
///
/// Declared with `final` and inference rather than an explicit type:
/// `FutureProvider.family` returns a `FutureProviderFamily<ValueT, ArgT>`, which
/// `flutter_riverpod` does not export, so the type cannot be named here.
final orderDetailProvider = FutureProvider.family<Order, String>(
  (Ref ref, String orderId) => ref.watch(orderRepositoryProvider).detail(orderId),
);

/// Order history. `role` is `passenger` or `driver` — the server resolves the
/// scope from the caller, not from this string.
final orderHistoryProvider = FutureProvider.family<OrderPage, String>(
  (Ref ref, String role) => ref.watch(orderRepositoryProvider).history(role: role),
);

/// Open orders near a point, for the driver's job list.
///
/// The record argument is a value type, so two identical centres share one
/// cache entry — important because the driver screen re-reads its position on a
/// timer and would otherwise refetch every tick.
final nearbyOrdersProvider =
    FutureProvider.family<NearbyOrders, ({double lat, double lng, double radiusKm})>(
      (Ref ref, ({double lat, double lng, double radiusKm}) centre) => ref
          .watch(orderRepositoryProvider)
          .nearby(lat: centre.lat, lng: centre.lng, radiusKm: centre.radiusKm),
    );

/// Admin: the KYC queue, filtered by status.
final adminDriversProvider = FutureProvider.family<Paged<AdminDriverRow>, DriverStatus?>(
  (Ref ref, DriverStatus? status) => ref.watch(adminRepositoryProvider).drivers(status: status),
);

/// Admin: the refund queue, filtered by status.
final adminRefundsProvider = FutureProvider.family<Paged<RefundRequest>, RefundStatus?>(
  (Ref ref, RefundStatus? status) => ref.watch(adminRepositoryProvider).refunds(status: status),
);

// --------------------------------------------------------------------------- //
// fleets
// --------------------------------------------------------------------------- //

/// The caller's fleet, or a null pair when they are not on a roster.
///
/// Not a 404 — "not in a fleet" is the common state, so `GET /fleets/me`
/// answers `{"fleet": null, "membership": null}` and this resolves to data.
final FutureProvider<MyFleet> myFleetProvider = FutureProvider<MyFleet>(
  (Ref ref) => ref.watch(fleetRepositoryProvider).myFleet(),
);

/// One fleet's roster. Read by both surfaces: a member sees their own fleet's
/// roster, an admin sees any fleet's.
final fleetMembersProvider = FutureProvider.family<List<FleetMember>, String>(
  (Ref ref, String fleetId) => ref.watch(fleetRepositoryProvider).members(fleetId),
);

/// One fleet's settlement history, newest week first.
final fleetSettlementProvider = FutureProvider.family<List<FleetSettlementRun>, String>(
  (Ref ref, String fleetId) => ref.watch(fleetRepositoryProvider).settlementHistory(fleetId),
);

/// Admin: the fleet register, filtered by operator status.
final adminFleetsProvider = FutureProvider.family<List<Fleet>, FleetStatus?>(
  (Ref ref, FleetStatus? status) async =>
      (await ref.watch(fleetRepositoryProvider).listFleets(status: status)).items,
);

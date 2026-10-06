import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../core/network/api_client.dart';
import '../core/storage/token_store.dart';
import '../data/admin_repository.dart';
import '../data/auth_repository.dart';
import '../data/destination_repository.dart';
import '../data/driver_notification_repository.dart';
import '../data/driver_repository.dart';
import '../data/fare_repository.dart';
import '../data/fleet_repository.dart';
import '../data/identity_repository.dart';
import '../data/order_repository.dart';
import '../data/recurring_ride_repository.dart';
import '../data/trip_repository.dart';
import '../models/auth.dart';
import 'auth_controller.dart';

/// Dependency graph. Everything is a plain [Provider] over a singleton-ish
/// object, so the only thing that ever changes identity is the session.

final Provider<TokenStore> tokenStoreProvider = Provider<TokenStore>(
  (Ref ref) => SecureTokenStore(),
);

final Provider<ApiClient> apiClientProvider = Provider<ApiClient>((Ref ref) {
  final ApiClient client = ApiClient(tokenStore: ref.watch(tokenStoreProvider));
  ref.onDispose(() => unawaited(client.close()));
  return client;
});

final Provider<AuthRepository> authRepositoryProvider = Provider<AuthRepository>(
  (Ref ref) => AuthRepository(ref.watch(apiClientProvider)),
);

/// Sign-in's sibling: the profile, and the phone unlock that calling a taxi
/// needs. Separate from [authRepositoryProvider] because proving a number is a
/// different operation from signing in — see `data/identity_repository.dart`.
final Provider<IdentityRepository> identityRepositoryProvider = Provider<IdentityRepository>(
  (Ref ref) => IdentityRepository(ref.watch(apiClientProvider)),
);

final Provider<FareRepository> fareRepositoryProvider = Provider<FareRepository>(
  (Ref ref) => FareRepository(ref.watch(apiClientProvider)),
);

final Provider<DestinationRepository> destinationRepositoryProvider =
    Provider<DestinationRepository>(
      (Ref ref) => DestinationRepository(ref.watch(apiClientProvider)),
    );

final Provider<OrderRepository> orderRepositoryProvider = Provider<OrderRepository>(
  (Ref ref) => OrderRepository(ref.watch(apiClientProvider)),
);

final Provider<DriverRepository> driverRepositoryProvider = Provider<DriverRepository>(
  (Ref ref) => DriverRepository(ref.watch(apiClientProvider)),
);

final Provider<DriverNotificationRepository> driverNotificationRepositoryProvider =
    Provider<DriverNotificationRepository>(
      (Ref ref) => DriverNotificationRepository(ref.watch(apiClientProvider)),
    );

final Provider<AdminRepository> adminRepositoryProvider = Provider<AdminRepository>(
  (Ref ref) => AdminRepository(ref.watch(apiClientProvider)),
);

final Provider<FleetRepository> fleetRepositoryProvider = Provider<FleetRepository>(
  (Ref ref) => FleetRepository(ref.watch(apiClientProvider)),
);

final Provider<TripRepository> tripRepositoryProvider = Provider<TripRepository>(
  (Ref ref) => TripRepository(ref.watch(apiClientProvider)),
);

/// Recurring rides: weekly templates the passenger owns. The scheduler is
/// server-side, so the repository never mints an order itself.
final Provider<RecurringRideRepository> recurringRideRepositoryProvider =
    Provider<RecurringRideRepository>(
      (Ref ref) => RecurringRideRepository(ref.watch(apiClientProvider)),
    );

/// The session. `AsyncValue<AppUser?>`: loading while the stored token is being
/// re-validated, `data(null)` when signed out, `data(user)` when signed in.
final AsyncNotifierProvider<AuthController, AppUser?> authControllerProvider =
    AsyncNotifierProvider<AuthController, AppUser?>(AuthController.new);

/// The signed-in account, or null. Throws if read before [authControllerProvider]
/// has settled — use `authControllerProvider` directly when that matters.
///
/// `AsyncValue.value` is nullable in Riverpod 3 (there is no `valueOrNull`), so
/// this collapses "loading" and "signed out" to null. Screens that need to tell
/// them apart must watch `authControllerProvider` itself.
final Provider<AppUser?> currentUserProvider = Provider<AppUser?>(
  (Ref ref) => ref.watch(authControllerProvider).value,
);

/// Convenience for screens that only care about who they are talking to.
final Provider<String?> currentUserIdProvider = Provider<String?>(
  (Ref ref) => ref.watch(currentUserProvider)?.id,
);

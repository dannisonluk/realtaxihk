import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/admin/admin_kyc_screen.dart';
import '../features/admin/admin_refunds_screen.dart';
import '../features/admin/admin_screen.dart';
import '../features/admin/admin_settlement_screen.dart';
import '../features/auth/login_screen.dart';
import '../features/auth/otp_screen.dart';
import '../features/driver/driver_active_trip_screen.dart';
import '../features/driver/driver_earnings_screen.dart';
import '../features/driver/driver_jobs_screen.dart';
import '../features/driver/driver_onboarding_screen.dart';
import '../features/driver/driver_screen.dart';
import '../features/passenger/passenger_screen.dart';
import '../features/passenger/request_ride_screen.dart';
import '../features/passenger/trip_history_screen.dart';
import '../features/passenger/trip_tracking_screen.dart';
import '../features/shared/account_screen.dart';
import '../features/splash_screen.dart';
import '../models/auth.dart';
import '../models/enums.dart';
import '../state/providers.dart';

/// Route paths, in one place so no screen has to spell a URL by hand.
abstract final class Routes {
  static const String splash = '/splash';
  static const String login = '/login';
  static const String otp = '/login/otp';

  static const String request = '/passenger/request';
  static const String trips = '/passenger/trips';
  static const String passengerAccount = '/passenger/account';
  static const String trackTrip = '/passenger/trip';

  static const String driverOnboarding = '/driver/onboarding';
  static const String driverJobs = '/driver/jobs';
  static const String driverAccount = '/driver/account';
  static const String driverEarnings = '/driver/earnings';
  static const String driverActiveTrip = '/driver/active';

  static const String adminKyc = '/admin/kyc';
  static const String adminRefunds = '/admin/refunds';
  static const String adminSettlement = '/admin/settlement';
  static const String adminAccount = '/admin/account';
}

final GlobalKey<NavigatorState> _rootNavigatorKey = GlobalKey<NavigatorState>(debugLabel: 'root');

/// Bridges a Riverpod provider to go_router's `refreshListenable`.
///
/// The alternative — `ref.watch(authControllerProvider)` inside the router
/// provider — rebuilds the `GoRouter` on every auth change, which throws away
/// the navigation stack. Listening keeps one router for the app's lifetime and
/// just re-runs `redirect`.
class _AuthListenable extends ChangeNotifier {
  _AuthListenable(Ref ref) {
    ref.listen(authControllerProvider, (AsyncValue<AppUser?>? previous, AsyncValue<AppUser?> next) {
      notifyListeners();
    });
  }
}

final Provider<GoRouter> routerProvider = Provider<GoRouter>((Ref ref) {
  final _AuthListenable listenable = _AuthListenable(ref);
  ref.onDispose(listenable.dispose);

  return GoRouter(
    navigatorKey: _rootNavigatorKey,
    initialLocation: Routes.splash,
    refreshListenable: listenable,
    redirect: (BuildContext context, GoRouterState state) => _redirect(ref, state),
    routes: <RouteBase>[
      GoRoute(
        path: Routes.splash,
        builder: (BuildContext context, GoRouterState state) => const SplashScreen(),
      ),
      GoRoute(
        path: Routes.login,
        builder: (BuildContext context, GoRouterState state) => const LoginScreen(),
        routes: <RouteBase>[
          GoRoute(
            path: 'otp',
            builder: (BuildContext context, GoRouterState state) {
              final Object? extra = state.extra;
              return OtpScreen(phoneE164: extra is String ? extra : '');
            },
          ),
        ],
      ),

      // ---- passenger -----------------------------------------------------
      StatefulShellRoute.indexedStack(
        builder: (BuildContext context, GoRouterState state, StatefulNavigationShell shell) =>
            PassengerScreen(shell: shell),
        branches: <StatefulShellBranch>[
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.request,
                builder: (BuildContext context, GoRouterState state) => const RequestRideScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.trips,
                builder: (BuildContext context, GoRouterState state) => const TripHistoryScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.passengerAccount,
                builder: (BuildContext context, GoRouterState state) => const AccountScreen(),
              ),
            ],
          ),
        ],
      ),
      GoRoute(
        // On the root navigator so the live map covers the bottom bar.
        path: '${Routes.trackTrip}/:orderId',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) =>
            TripTrackingScreen(orderId: state.pathParameters['orderId']!),
      ),

      // ---- driver --------------------------------------------------------
      GoRoute(
        path: Routes.driverOnboarding,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const DriverOnboardingScreen(),
      ),
      GoRoute(
        path: '${Routes.driverActiveTrip}/:orderId',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) =>
            DriverActiveTripScreen(orderId: state.pathParameters['orderId']!),
      ),
      StatefulShellRoute.indexedStack(
        builder: (BuildContext context, GoRouterState state, StatefulNavigationShell shell) =>
            DriverScreen(shell: shell),
        branches: <StatefulShellBranch>[
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.driverJobs,
                builder: (BuildContext context, GoRouterState state) => const DriverJobsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.driverEarnings,
                builder: (BuildContext context, GoRouterState state) =>
                    const DriverEarningsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.driverAccount,
                builder: (BuildContext context, GoRouterState state) =>
                    const AccountScreen(driverMode: true),
              ),
            ],
          ),
        ],
      ),

      // ---- admin ---------------------------------------------------------
      StatefulShellRoute.indexedStack(
        builder: (BuildContext context, GoRouterState state, StatefulNavigationShell shell) =>
            AdminScreen(shell: shell),
        branches: <StatefulShellBranch>[
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.adminKyc,
                builder: (BuildContext context, GoRouterState state) => const AdminKycScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.adminRefunds,
                builder: (BuildContext context, GoRouterState state) => const AdminRefundsScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.adminSettlement,
                builder: (BuildContext context, GoRouterState state) =>
                    const AdminSettlementScreen(),
              ),
            ],
          ),
          StatefulShellBranch(
            routes: <RouteBase>[
              GoRoute(
                path: Routes.adminAccount,
                builder: (BuildContext context, GoRouterState state) => const AccountScreen(),
              ),
            ],
          ),
        ],
      ),
    ],
  );
});

/// Where an authenticated account belongs.
///
/// **Only ADMIN is a real account role.** `app/models/__init__.py` defines
/// `UserRole.DRIVER`, but nothing in the backend ever assigns it — signup always
/// creates a PASSENGER (`otp_service.py`), `POST /drivers/register` only creates
/// a `DriverProfile`, and `require_role()` in `app/api/auth.py` is never called.
/// Driver capability is gated entirely on the *profile*: `grab` and
/// `POST /driver/location` require `DriverStatus.ACTIVE`, and the rest of
/// `/drivers/me/*` requires the profile to exist.
///
/// So the routing model is: admin gets the console, everyone else gets the
/// passenger app, and driver mode is entered from the account screen once a
/// profile exists. An account can legitimately be both.
String _homeFor(AppUser user) => user.role == UserRole.admin ? Routes.adminKyc : Routes.request;

String? _redirect(Ref ref, GoRouterState state) {
  final AsyncValue<AppUser?> auth = ref.read(authControllerProvider);
  final String location = state.uri.path;

  // Cold start: the stored token is still being re-validated. Hold on the splash
  // rather than flashing the login screen at a signed-in user.
  final bool restoring = auth.isLoading && !auth.hasValue;
  if (restoring) {
    return location == Routes.splash ? null : Routes.splash;
  }

  final AppUser? user = auth.value;

  if (user == null) {
    // A restore failure (offline) leaves `hasError`, and the splash screen owns
    // the retry — do not bounce to login, which would look like a sign-out.
    if (auth.hasError) {
      return location == Routes.splash ? null : Routes.splash;
    }
    return location.startsWith(Routes.login) ? null : Routes.login;
  }

  final bool isAdmin = user.role == UserRole.admin;
  final String home = _homeFor(user);

  // Sitting on a pre-auth route while signed in.
  if (location == Routes.splash || location.startsWith(Routes.login)) {
    return home;
  }

  // An admin account has no passenger or driver surface, and vice versa: the
  // driver endpoints would 403 on a missing profile and the admin ones on a
  // missing ADMIN row, so neither branch is reachable for the wrong account.
  final bool inAdminArea = location.startsWith('/admin');
  if (isAdmin && !inAdminArea) {
    return home;
  }
  if (!isAdmin && inAdminArea) {
    return home;
  }

  return null;
}

import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';

import '../features/admin/admin_fleet_detail_screen.dart';
import '../features/admin/admin_fleets_screen.dart';
import '../features/admin/admin_kyc_screen.dart';
import '../features/admin/admin_refunds_screen.dart';
import '../features/admin/admin_screen.dart';
import '../features/admin/admin_settlement_screen.dart';
import '../features/auth/change_password_screen.dart';
import '../features/auth/forgot_password_screen.dart';
import '../features/auth/login_screen.dart';
import '../features/auth/otp_screen.dart';
import '../features/auth/password_reset_screen.dart';
import '../features/auth/phone_login_screen.dart';
import '../features/auth/phone_unlock_screen.dart';
import '../features/auth/register_screen.dart';
import '../features/driver/driver_active_trip_screen.dart';
import '../features/driver/driver_earnings_screen.dart';
import '../features/driver/driver_environment_screen.dart';
import '../features/driver/driver_jobs_screen.dart';
import '../features/driver/driver_notifications_screen.dart';
import '../features/driver/driver_onboarding_screen.dart';
import '../features/driver/driver_screen.dart';
import '../features/driver/fixed_offers_screen.dart';
import '../features/fleet/fleet_screen.dart';
import '../features/passenger/passenger_screen.dart';
import '../features/passenger/recurring_rides_screen.dart';
import '../features/passenger/request_ride_screen.dart';
import '../features/passenger/trip_detail_screen.dart';
import '../features/passenger/trip_history_screen.dart';
import '../features/passenger/trip_tracking_screen.dart';
import '../features/shared/account_screen.dart';
import '../features/shared/profile_setup_screen.dart';
import '../features/splash_screen.dart';
import '../models/auth.dart';
import '../models/order.dart';
import '../state/providers.dart';
import 'routing_rules.dart';

// `Routes` and the redirect rules live in `routing_rules.dart` so they can be
// tested without a widget tree. Re-exported here because every screen already
// imports this file for `Routes`.
export 'routing_rules.dart' show Routes;

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

  final String initialLocation =
      passwordResetDeepLinkRoute(
        WidgetsBinding.instance.platformDispatcher.defaultRouteName,
      ) ??
      Routes.splash;

  return GoRouter(
    navigatorKey: _rootNavigatorKey,
    initialLocation: initialLocation,
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
            path: 'register',
            builder: (BuildContext context, GoRouterState state) => const RegisterScreen(),
          ),
          GoRoute(
            path: 'phone',
            builder: (BuildContext context, GoRouterState state) => const PhoneLoginScreen(),
          ),
          GoRoute(
            path: 'forgot',
            builder: (BuildContext context, GoRouterState state) => const ForgotPasswordScreen(),
          ),
          GoRoute(
            path: 'otp',
            builder: (BuildContext context, GoRouterState state) {
              // `state.extra` is null when `/login/otp` is reached directly (a
              // deep link, a hot reload onto the location) rather than by
              // pushing from the phone screen.
              //
              // An OTP screen with no phone number is a state with no meaning:
              // it renders "code sent to " with nothing after it, and "resend"
              // posts an invalid number, so the user sees a server error whose
              // real cause is the route they arrived by. Send them back to
              // login instead of rendering the half-screen.
              final Object? extra = state.extra;
              final String phone = extra is String ? extra : '';
              if (phone.isEmpty) {
                return const LoginScreen();
              }
              return OtpScreen(phoneE164: phone);
            },
          ),
        ],
      ),
      GoRoute(
        // The phone unlock. On the root navigator and **outside** `/login`: the
        // redirect sends a signed-in user away from every pre-auth path, and the
        // accounts that need this screen are exactly the ones already signed in.
        path: Routes.phoneUnlock,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const PhoneUnlockScreen(),
      ),

      GoRoute(
        // Password reset from an email App Link. Root navigator and **outside**
        // `/login` for the same reason as the phone unlock: the reset must work
        // for signed-out users, and a signed-in user with a valid token should
        // not be bounced back to the home screen.
        path: Routes.passwordReset,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) =>
            PasswordResetScreen(token: state.uri.queryParameters['token']),
      ),

      GoRoute(
        // The profile form. Root navigator and **outside** `/login`, like the
        // phone unlock — see `Routes.profileSetup` for why it is not a gate.
        path: Routes.profileSetup,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const ProfileSetupScreen(),
      ),

      GoRoute(
        // Change password. Root navigator and outside `/login`, like the phone
        // unlock: the account that needs it is already signed in, and the
        // redirect sends a signed-in user away from every `/login` path.
        path: Routes.passwordChange,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const ChangePasswordScreen(),
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
                builder: (BuildContext context, GoRouterState state) {
                  final Object? extra = state.extra;
                  if (extra is OrderCreateRequest) {
                    return RequestRideScreen(prefill: extra);
                  }
                  return const RequestRideScreen();
                },
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
      GoRoute(
        // Also on the root navigator: reached from the history list and from
        // the live map, so it must not be tied to a single shell branch.
        path: '${Routes.tripDetail}/:orderId',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) =>
            TripDetailScreen(orderId: state.pathParameters['orderId']!),
      ),
      GoRoute(
        // Recurring rides are a passenger settings surface, opened from the
        // trip history "repeat weekly" action. Root navigator so the bottom
        // bar does not stay mounted while the form is in use.
        path: Routes.recurringRides,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) {
          final Object? extra = state.extra;
          if (extra is RecurringRidePrefill) {
            return RecurringRideScreen(prefill: extra.order, sourceOrderId: extra.sourceOrderId);
          }
          if (extra is OrderCreateRequest) {
            return RecurringRideScreen(prefill: extra);
          }
          return const RecurringRideScreen();
        },
      ),

      // ---- driver --------------------------------------------------------
      GoRoute(
        path: Routes.driverOnboarding,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const DriverOnboardingScreen(),
      ),
      GoRoute(
        // The driver's in-app notification inbox (一口價 / premium alerts).
        // Root navigator so the badge action on the jobs screen can push it.
        path: Routes.driverNotifications,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const DriverNotificationsScreen(),
      ),
      GoRoute(
        path: '${Routes.driverActiveTrip}/:orderId',
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) =>
            DriverActiveTripScreen(orderId: state.pathParameters['orderId']!),
      ),
      GoRoute(
        // Read-only, so it sits outside the shell: a driver dips in to check
        // their roster and fee, then comes back.
        path: Routes.driverFleet,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const FleetScreen(),
      ),
      GoRoute(
        // Like [Routes.driverFleet]: a settings form outside the shell, reached
        // from the driver account screen.
        path: Routes.driverEnvironment,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const DriverEnvironmentScreen(),
      ),
      GoRoute(
        // Like [Routes.driverFleet]: a pricing form outside the shell, reached
        // from the driver account screen, and managed as a settings page.
        path: Routes.fixedOffers,
        parentNavigatorKey: _rootNavigatorKey,
        builder: (BuildContext context, GoRouterState state) => const FixedOffersScreen(),
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
                path: Routes.adminFleets,
                builder: (BuildContext context, GoRouterState state) => const AdminFleetsScreen(),
                routes: <RouteBase>[
                  // Nested inside the branch so the bottom bar stays put while
                  // working through a fleet's roster and settlement.
                  GoRoute(
                    path: ':fleetId',
                    builder: (BuildContext context, GoRouterState state) =>
                        AdminFleetDetailScreen(fleetId: state.pathParameters['fleetId']!),
                  ),
                ],
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

/// The redirect decision, adapted from Riverpod state to [resolveRedirect].
///
/// The rules themselves live in `routing_rules.dart`; this only reads the auth
/// provider and unpacks `AsyncValue` into the three flags the rule needs.
String? _redirect(Ref ref, GoRouterState state) {
  final AsyncValue<AppUser?> auth = ref.read(authControllerProvider);
  return resolveRedirect(
    location: state.uri.path,
    // Cold start: the stored token is still being re-validated.
    restoring: auth.isLoading && !auth.hasValue,
    hasError: auth.hasError,
    user: auth.value,
  );
}

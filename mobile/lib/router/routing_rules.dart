/// Routing rules, as pure functions.
///
/// These live apart from `app_router.dart` so they can be exercised without a
/// widget tree. `flutter test` does not run on this machine (see
/// `mobile/README.md`), and the redirect is the piece of the app most likely to
/// strand a user on a blank screen — a signed-in driver bounced to login, or an
/// admin bounced into the passenger shell — so it is worth being able to test
/// it directly. `tool/run_tests.dart` does exactly that.
library;

import '../models/auth.dart';
import '../models/enums.dart';

/// Route paths, in one place so no screen has to spell a URL by hand.
abstract final class Routes {
  static const String splash = '/splash';
  static const String login = '/login';

  /// Create an account. Nested under [login] so `resolveRedirect`'s existing
  /// `location.startsWith(Routes.login)` case keeps it pre-auth — a sibling path
  /// would need a second rule, and two rules for one idea drift.
  static const String register = '/login/register';

  /// The **secondary** door: an already-proven number plus a code.
  static const String phoneLogin = '/login/phone';

  static const String otp = '/login/otp';

  /// Request a password-reset link. Nested under [login] so the existing
  /// `location.startsWith(Routes.login)` case keeps it pre-auth: the person who
  /// needs it is, by definition, unable to sign in.
  static const String passwordForgot = '/login/forgot';

  /// Changing the password of an account that is already signed in.
  ///
  /// **Deliberately not under `/login`**, for the same reason as [phoneUnlock]:
  /// `resolveRedirect` sends a signed-in user away from every `/login` path, so
  /// a route there would be unreachable for exactly the accounts that need it.
  static const String passwordChange = '/password/change';

  /// Proving a phone number — the call車 unlock.
  ///
  /// **Deliberately not under `/login`.** `resolveRedirect` sends a signed-in user
  /// away from every `/login` path, so a route there would be unreachable for
  /// exactly the accounts that need it: the ones already signed in without a
  /// proven number. It is mounted on the root navigator, like
  /// [driverOnboarding], because it is reached from the passenger account screen,
  /// from the driver account screen, and from a 403 on the ride-request screen.
  static const String phoneUnlock = '/phone/unlock';

  /// Filling in the account's own record — username, names, gender.
  ///
  /// Mounted on the root navigator and **outside `/login`**, for the same reason
  /// as [phoneUnlock]: the accounts that need it are already signed in, and
  /// `resolveRedirect` sends a signed-in user away from every `/login` path.
  ///
  /// It is deliberately **not** a gate, and there is no rule for it below. The
  /// server treats a missing username as an incomplete profile rather than as a
  /// refusal — `account_status` is a completeness flag, and no dependency in
  /// `app/core/deps.py` reads it — so a client-side redirect here would be
  /// stricter than the API and would strand a user on a screen the backend was
  /// happy to serve. The screen is *offered*, from the account screen and from
  /// the booking screen, and the user may walk away from it.
  static const String profileSetup = '/profile/setup';

  static const String request = '/passenger/request';
  static const String trips = '/passenger/trips';
  static const String passengerAccount = '/passenger/account';
  static const String recurringRides = '/passenger/recurring';
  static const String trackTrip = '/passenger/trip';

  /// The frozen receipt for one order. A sibling of [trackTrip] rather than a
  /// child of it: the live map and the record answer different questions, and a
  /// terminal order only has the latter.
  static const String tripDetail = '/passenger/order';

  static const String driverOnboarding = '/driver/onboarding';
  static const String driverJobs = '/driver/jobs';
  static const String driverAccount = '/driver/account';
  static const String driverEarnings = '/driver/earnings';
  static const String driverActiveTrip = '/driver/active';

  /// The driver's in-app notification inbox. Pushed on the root navigator from
  /// the jobs screen, like [fixedOffers]: a surface the driver dips into
  /// between rides, not a branch of the driver shell.
  static const String driverNotifications = '/driver/notifications';

  /// The driver's priced standing routes (一口價). Pushed on the root
  /// navigator from the driver account screen, like [driverFleet]: it is a
  /// settings page the driver manages outside the driver shell.
  static const String fixedOffers = '/driver/fixed-offers';

  /// The driver's own fleet (的士車隊), if they are on a roster. Pushed on the
  /// root navigator from the account screen, like onboarding.
  static const String driverFleet = '/driver/fleet';

  /// The driver's declared in-car environment (靜音 / 無煙 / 無香水 / 不播音樂).
  ///
  /// Pushed on the root navigator from the driver account screen, like
  /// [driverFleet]: it is a settings form the driver dips into and leaves, not a
  /// branch of the driver shell.
  static const String driverEnvironment = '/driver/environment';

  static const String adminKyc = '/admin/kyc';
  static const String adminRefunds = '/admin/refunds';
  static const String adminSettlement = '/admin/settlement';
  static const String adminFleets = '/admin/fleets';
  static const String adminAccount = '/admin/account';
}

/// Where an authenticated account belongs.
///
/// **Only ADMIN is a real account role.** `app/models/__init__.py` defines
/// `UserRole.DRIVER`, but nothing in the backend ever assigns it — signup always
/// creates a PASSENGER (`app/services/auth/account_service.py`),
/// `POST /drivers/register` only creates a `DriverProfile`, and `require_role()`
/// in `app/api/auth.py` is never called. Driver capability is gated entirely on
/// the *profile*: `grab` and `POST /drivers/location` require
/// `DriverStatus.ACTIVE`, and the rest of `/drivers/me/*` requires the profile to
/// exist.
///
/// So the routing model is: admin gets the console, everyone else gets the
/// passenger app, and driver mode is entered from the account screen once a
/// profile exists. An account can legitimately be both.
String homeRouteFor(AppUser user) => user.role == UserRole.admin ? Routes.adminKyc : Routes.request;

/// The router's redirect decision.
///
/// Returns the location to go to, or null to stay put.
///
/// * [restoring] — a stored session is still being re-validated. Hold on the
///   splash rather than flashing the login screen at a signed-in user.
/// * [hasError] — the restore failed (typically offline). The splash owns the
///   retry; bouncing to login here would look like a sign-out and would throw
///   away tokens that are still perfectly good.
String? resolveRedirect({
  required String location,
  required bool restoring,
  required bool hasError,
  required AppUser? user,
}) {
  if (restoring) {
    return location == Routes.splash ? null : Routes.splash;
  }

  if (user == null) {
    if (hasError) {
      return location == Routes.splash ? null : Routes.splash;
    }
    return location.startsWith(Routes.login) ? null : Routes.login;
  }

  final bool isAdmin = user.role == UserRole.admin;
  final String home = homeRouteFor(user);

  // Sitting on a pre-auth route while signed in.
  if (location == Routes.splash || location.startsWith(Routes.login)) {
    return home;
  }

  // Changing a password is not a role-specific surface. Both an admin and a
  // passenger own one, and the mobile admin shell renders the *same*
  // `AccountScreen` — so without this carve-out the split below would bounce an
  // admin straight back to the console and the tile would be a dead affordance.
  // `/password/change` is deliberately not under `/login`, which is why it is
  // not already covered by the branch above.
  if (location.startsWith(Routes.passwordChange)) {
    return null;
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

import 'dart:async';

import 'package:flutter/cupertino.dart' show CupertinoAlertDialog, CupertinoDialogAction;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../../core/format/money.dart';
import '../../core/location/location_service.dart';
import '../../core/network/api_exception.dart';
import '../../core/theme/app_theme.dart';
import '../../models/enums.dart';

/// Renders an [AsyncValue] with a spinner, a retryable error, and a body.
///
/// Every screen uses this so a failure looks the same everywhere and always
/// offers the same way out. [onRetry] is what the retry button calls — pass
/// `ref.invalidate(provider)` for a `FutureProvider`, or a controller method for
/// an action.
class AsyncValueView<T> extends StatelessWidget {
  const AsyncValueView({
    required this.value,
    required this.builder,
    this.onRetry,
    this.loading,
    super.key,
  });

  final AsyncValue<T> value;
  final Widget Function(T data) builder;
  final VoidCallback? onRetry;
  final Widget? loading;

  @override
  Widget build(BuildContext context) {
    return value.when(
      skipLoadingOnRefresh: false,
      data: builder,
      loading: () => loading ?? const Center(child: CircularProgressIndicator()),
      error: (Object error, StackTrace stack) => ErrorView(error: error, onRetry: onRetry),
    );
  }
}

/// The brand poster, rendered [size] logical pixels square.
///
/// The artwork carries the wordmark *in the pixels* — there is no vector mark to
/// compose from — so this is an [Image] and not a `Row` of an icon and a `Text`.
/// `mobile/tool/gen_branding_assets.py` generates the file this loads; run it
/// after the artwork changes.
///
/// Three deliberate choices:
///
///  * **Corners are rounded** by `size * 0.22`, which lands near the squircle
///    iOS masks an app icon to. The poster's own background is a flat `#D9DDE6`
///    that matches neither appearance's scaffold, so left square it reads as a
///    grey tile pasted onto the page rather than as the app's own mark.
///  * **The locale picks the file.** The wordmark is baked into the art
///    ("香港Call的士" vs "HKFASTDC"), so there is nothing to localise at runtime
///    — there are two files and we choose one. `HkfastdcApp` pins the locale to
///    `zh-HK` today, so this resolves to the Chinese poster in practice.
///  * **The whole thing is one semantics node.** A poster whose text lives in
///    the pixels is one object to a screen reader, not a picture followed by a
///    stray wordmark. [semanticLabel] is that object's name, and it is also the
///    wordmark the fallback below draws.
class BrandLogo extends StatelessWidget {
  const BrandLogo({required this.size, this.semanticLabel = '香港Call的士', super.key});

  /// Width and height in logical pixels. The source is 1024px, so anything up
  /// to ~340dp is a downscale — which is the case [FilterQuality.medium] is
  /// built for. It mipmaps; `FilterQuality.high` is documented as *worse* than
  /// `medium` below 0.5x, and this runs at ~0.23x.
  final double size;

  /// What a screen reader announces, and the wordmark the fallback draws.
  final String semanticLabel;

  static String _asset() {
    // The app currently ships Chinese-only copy, so the in-app wordmark is
    // always the Chinese one. `logo-en.webp` remains generated for a future
    // English UI, but there is no branch to it while no English resources exist.
    return 'assets/branding/logo-zh.webp';
  }

  @override
  Widget build(BuildContext context) {
    return Semantics(
      image: true,
      label: semanticLabel,
      child: ClipRRect(
        borderRadius: BorderRadius.circular(size * 0.22),
        child: Image.asset(
          _asset(),
          width: size,
          height: size,
          filterQuality: FilterQuality.medium,
          excludeFromSemantics: true,
          // A missing asset means the generator was never run. Draw the mark the
          // app used before — icon plus wordmark — rather than Flutter's grey
          // error box on the first screen a user ever sees.
          errorBuilder: (BuildContext context, Object error, StackTrace? stack) => SizedBox(
            width: size,
            height: size,
            child: Column(
              mainAxisAlignment: MainAxisAlignment.center,
              children: <Widget>[
                Icon(
                  Icons.local_taxi_rounded,
                  size: size * 0.5,
                  color: Theme.of(context).colorScheme.primary,
                ),
                const SizedBox(height: AppTheme.space2),
                Text(semanticLabel, style: Theme.of(context).textTheme.titleMedium),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// A failure with the server's own message and, where it helps, a hint about
/// what to do next.
///
/// The shape follows Apple's guidance for a status screen (`feedback.md`,
/// `writing.md`): an icon, what happened, what to do about it, and one action.
/// The message is the server's own, because a generic "something went wrong"
/// tells a driver on the street nothing they can act on. Copy follows the
/// interface's voice — it says what happened, not that it is sorry.
class ErrorView extends StatelessWidget {
  const ErrorView({required this.error, this.onRetry, super.key});

  final Object error;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final String hint = switch (error) {
      final ApiException e when e.code == ApiException.rateLimited =>
        e.retryAfter == null ? '請稍候片刻再試。' : '請等 ${(e.retryAfter!.inSeconds / 60).ceil()} 分鐘後再試。',
      final ApiException e when e.code == ApiException.serviceUnavailable => '系統目前繁忙，請稍後再試。',
      final ApiException e when e.code == ApiException.network => '請檢查網絡連線，剛才的操作並未送出。',
      _ => '',
    };

    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.error_outline, size: 44, color: theme.colorScheme.error),
            const SizedBox(height: AppTheme.space4),
            Text(error.userMessage, textAlign: TextAlign.center, style: theme.textTheme.bodyLarge),
            if (hint.isNotEmpty) ...<Widget>[
              const SizedBox(height: AppTheme.space2),
              Text(hint, textAlign: TextAlign.center, style: theme.textTheme.bodySmall),
            ],
            if (onRetry != null) ...<Widget>[
              const SizedBox(height: AppTheme.space6),
              // A prominent action, per `buttons.md`: one obvious thing to do.
              FilledButton.tonal(onPressed: onRetry, child: const Text('請再試')),
            ],
          ],
        ),
      ),
    );
  }
}

/// An empty state with an optional call to action.
///
/// Apple's rule (`writing.md › Empty states`): an empty screen invites the next
/// action rather than reporting an absence. Where there is a natural next step,
/// [action] is that step, not a "dismiss".
class EmptyView extends StatelessWidget {
  const EmptyView({required this.icon, required this.title, this.subtitle, this.action, super.key});

  final IconData icon;
  final String title;
  final String? subtitle;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(AppTheme.space8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(icon, size: 44, color: theme.colorScheme.onSurfaceVariant),
            const SizedBox(height: AppTheme.space4),
            Text(title, style: theme.textTheme.titleMedium, textAlign: TextAlign.center),
            if (subtitle != null) ...<Widget>[
              const SizedBox(height: AppTheme.space2),
              Text(subtitle!, textAlign: TextAlign.center, style: theme.textTheme.bodySmall),
            ],
            if (action != null) ...<Widget>[const SizedBox(height: AppTheme.space6), action!],
          ],
        ),
      ),
    );
  }
}

/// A grouped list section: a titled card holding a run of rows.
///
/// This is iOS's grouped table (`lists-and-tables.md`): related rows share one
/// rounded surface, the section has a small uppercase-ish header, and the seam
/// between sections is the background showing through. Grouping is what tells a
/// reader which rows belong together — Apple calls it out in `layout.md ›
/// Visual hierarchy` ("Group related items to clearly express related
/// information or functions").
class GroupedSection extends StatelessWidget {
  const GroupedSection({required this.title, required this.children, this.footnote, super.key});

  final String title;
  final List<Widget> children;

  /// A short line under the card, for a caveat or a source. Optional.
  final String? footnote;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.only(left: AppTheme.space4, bottom: AppTheme.space2),
          child: Text(
            title,
            style: theme.textTheme.labelLarge?.copyWith(color: theme.colorScheme.onSurfaceVariant),
          ),
        ),
        Card(
          child: Padding(
            padding: const EdgeInsets.symmetric(
              horizontal: AppTheme.space4,
              vertical: AppTheme.space3,
            ),
            child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: children),
          ),
        ),
        if (footnote != null)
          Padding(
            padding: const EdgeInsets.only(
              left: AppTheme.space4,
              right: AppTheme.space4,
              top: AppTheme.space2,
            ),
            child: Text(footnote!, style: theme.textTheme.labelMedium),
          ),
      ],
    );
  }
}

/// An amount, coloured by sign — see [AppTheme.moneyColor]. Credit is lime/
/// green, debit amber; red is deliberately absent from the product.
class MoneyText extends StatelessWidget {
  const MoneyText(this.money, {this.signed = false, this.style, this.showSymbol = true, super.key});

  final Money money;

  /// Prefix with an explicit `+`/`-`, for a ledger row.
  final bool signed;

  final TextStyle? style;
  final bool showSymbol;

  @override
  Widget build(BuildContext context) {
    final String text = signed ? money.signedHkd : (showSymbol ? money.hkd : money.display);
    return Text(
      text,
      style: (style ?? Theme.of(context).textTheme.titleMedium)?.copyWith(
        color: signed ? AppTheme.moneyColor(context, money.asDouble) : null,
        fontWeight: FontWeight.w600,
        fontFeatures: const <FontFeature>[FontFeature.tabularFigures()],
      ),
    );
  }
}

/// A small status pill.
class StatusChip extends StatelessWidget {
  const StatusChip({required this.label, required this.color, this.semanticsLabel, super.key});

  factory StatusChip.order(OrderStatus status, BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return StatusChip(
      label: status.labelZh,
      semanticsLabel: '訂單狀態：${status.labelZh}',
      color: switch (status) {
        OrderStatus.created => scheme.onSurfaceVariant,
        OrderStatus.broadcasting => AppTheme.pending,
        // P4: waiting on the passenger to confirm boarding — still "pending",
        // but the driver has already done their part.
        OrderStatus.pendingArrivalConfirm => AppTheme.pending,
        OrderStatus.accepted || OrderStatus.driverArrived => scheme.primary,
        // `DESTINATION_CHANGED` is a live trip that happens to have a new
        // dropoff, so it reads the same as `IN_TRIP`.
        OrderStatus.inTrip || OrderStatus.destinationChanged => AppTheme.statusLive,
        // Neutral rather than a colour: the trip is over and nothing is
        // outstanding. This used to be `loss`, which painted a finished trip as
        // money leaving the account and disagreed with the console, where the
        // same state is neutral.
        OrderStatus.completed => scheme.onSurfaceVariant,
        // Interrupted and cancelled are both "did not complete"; the label
        // carries the distinction (a trip that ended early vs one that never
        // departed), and the settlement difference is not a UI colour.
        OrderStatus.cancelled || OrderStatus.interrupted => scheme.error,
      },
    );
  }

  factory StatusChip.driver(DriverStatus status, BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return StatusChip(
      label: status.labelZh,
      semanticsLabel: '司機狀態：${status.labelZh}',
      color: switch (status) {
        DriverStatus.pendingKyc => AppTheme.pending,
        DriverStatus.depositRequired => scheme.primary,
        // A driver who is working, not money leaving an account.
        DriverStatus.active => AppTheme.statusLive,
        DriverStatus.suspended => scheme.error,
        DriverStatus.terminated => scheme.onSurfaceVariant,
      },
    );
  }

  factory StatusChip.refund(RefundStatus status, BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return StatusChip(
      label: status.labelZh,
      semanticsLabel: '退款狀態：${status.labelZh}',
      color: switch (status) {
        RefundStatus.pending => AppTheme.pending,
        // Here a money colour is the right one: approval means cash going back
        // to the passenger, which is what green-down means in this app.
        RefundStatus.approved => AppTheme.loss,
        RefundStatus.rejected => scheme.error,
      },
    );
  }

  final String label;
  final Color color;

  /// What a screen reader announces instead of [label].
  ///
  /// A pill's word on its own ("行程中") leaves the listener guessing what it
  /// describes, so the factories name the state it belongs to. Null means the
  /// label already stands on its own.
  final String? semanticsLabel;

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    // 13 pt / w600 is Footnote with an emphasis, which is the size iOS uses for
    // a status pill. The tint is a fill plus a border rather than text colour
    // alone, so the state survives for someone who cannot tell the hues apart
    // (`accessibility.md › Vision`: convey information with more than colour).
    return Semantics(
      label: semanticsLabel ?? label,
      // The pill's own text says the same thing; announcing both would read the
      // state twice.
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: AppTheme.space3, vertical: 3),
          decoration: BoxDecoration(
            color: color.withValues(alpha: 0.14),
            borderRadius: BorderRadius.circular(999),
            border: Border.all(color: color.withValues(alpha: 0.5)),
          ),
          child: Text(
            label,
            style: theme.textTheme.labelLarge?.copyWith(color: color, fontWeight: FontWeight.w600),
          ),
        ),
      ),
    );
  }
}

/// A label/value row used across the detail and receipt screens.
/// Supply either [value] or [valueWidget], whichever fits.
///
/// The label column is a fixed 132 pt at the default text size rather than
/// intrinsic, so stacked rows align — the value edge is the thing a reader scans
/// down, and it has to be straight (`layout.md › Visual hierarchy`: "Align
/// elements to make them easier to scan"). Above 1.3x that fixed width turns
/// into a trap: the label wraps to three lines or clips its last word while the
/// value column keeps all of its room, so the column gives way to legibility
/// instead.
class DetailRow extends StatelessWidget {
  const DetailRow({required this.label, this.value, this.valueWidget, super.key});

  final String label;
  final String? value;
  final Widget? valueWidget;

  /// The text size the user has asked for. `1.0` is the system default.
  static double _textScale(BuildContext context) => MediaQuery.textScalerOf(context).scale(1);

  @override
  Widget build(BuildContext context) {
    final ThemeData theme = Theme.of(context);
    final bool largeType = _textScale(context) > 1.3;
    final Widget labelText = Text(label, style: theme.textTheme.bodyMedium);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: AppTheme.space2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          if (largeType)
            Flexible(flex: 4, child: labelText)
          else
            SizedBox(width: 132, child: labelText),
          const SizedBox(width: AppTheme.space3),
          Expanded(
            flex: largeType ? 5 : 1,
            child:
                valueWidget ??
                Text(
                  value ?? '—',
                  style: theme.textTheme.bodyMedium?.copyWith(fontWeight: FontWeight.w500),
                ),
          ),
        ],
      ),
    );
  }
}

/// Shorthand for a transient failure toast.
void showError(BuildContext context, Object error) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(error.userMessage)));
}

/// Shorthand for a success toast.
void showInfo(BuildContext context, String message) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(message)));
}

/// A refusal with a way out, for a failure whose fix is on another screen.
///
/// [showError] is right when the user can simply retry from where they are. It is
/// wrong when the server refused for a reason that has to be resolved elsewhere —
/// a 403 `PHONE_NOT_VERIFIED` on a booking, say — because a bare message leaves
/// the user holding a form that will keep failing, with no route to the fix.
///
/// A `SnackBar` action rather than a dialog: the user is mid-form, the form is
/// still valid, and a modal would take it away to ask a question with one obvious
/// answer. The longer duration is because there is now something to read and then
/// act on.
void showErrorAction(
  BuildContext context,
  Object error, {
  required String actionLabel,
  required VoidCallback onAction,
}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(error.userMessage),
        duration: const Duration(seconds: 8),
        action: SnackBarAction(label: actionLabel, onPressed: onAction),
      ),
    );
}

/// An informational message with a way out, for a fix that is elsewhere.
///
/// [showInfo] is right when reading the message is all the user has to do. It is
/// wrong when the fix lives on a screen the app cannot reach: the message then
/// names a problem and leaves the user to go find the setting.
void showInfoAction(
  BuildContext context,
  String message, {
  required String actionLabel,
  required VoidCallback onAction,
}) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(
      SnackBar(
        content: Text(message),
        duration: const Duration(seconds: 8),
        action: SnackBarAction(label: actionLabel, onPressed: onAction),
      ),
    );
}

/// Whether the fix for [access] lives in the system settings, not in this app.
bool locationNeedsSettings(LocationAccess access) =>
    access == LocationAccess.deniedForever || access == LocationAccess.serviceDisabled;

/// What to tell a user whose location could not be read, for [access].
///
/// [alternative] names what they can still do instead — the pickup map takes a
/// tap, so a passenger without location can still book — for the screens where a
/// refusal is not a wall.
///
/// Kept separate from [showLocationUnavailable] because the driver's jobs screen
/// states the same thing inline, under the online switch, rather than in a
/// snackbar that slides away.
String locationRefusalMessage(LocationAccess access, {String? alternative}) {
  final String tail = alternative == null ? '' : ' $alternative';
  return switch (access) {
    // Permission is fine, so the settings hold nothing to change: the read
    // itself failed, and asking again is the only advice worth giving.
    LocationAccess.granted => '未能取得位置，請再試一次。$tail',
    // Transient — the system dialog returns on the next ask, so the way out is
    // the button, not the settings.
    LocationAccess.denied => '未取得定位權限，再按一次即可授權。$tail',
    LocationAccess.deniedForever => '定位權限已被拒絕，需在設定中重新開啟。$tail',
    LocationAccess.serviceDisabled => '手機的定位服務未開啟，需在設定中開啟。$tail',
  };
}

/// Reports that no location fix could be taken, with a route out where one
/// exists.
///
/// The four [LocationAccess] outcomes used to be flattened into one toast, and
/// two of them are dead ends: a permanently-denied permission and a disabled
/// location service can only be fixed in the system settings, and nothing in the
/// app said so. The user tapped "定位", read "未能取得位置", and had no way to
/// learn the app had been refused for good.
void showLocationUnavailable(
  BuildContext context,
  LocationAccess access, {
  required Future<void> Function() onOpenSettings,
  Future<void> Function()? onRetry,
  String? alternative,
}) {
  final String message = locationRefusalMessage(access, alternative: alternative);
  if (locationNeedsSettings(access)) {
    showInfoAction(
      context,
      message,
      actionLabel: '去設定',
      onAction: () => unawaited(onOpenSettings()),
    );
  } else if (onRetry != null) {
    showInfoAction(context, message, actionLabel: '重新授權', onAction: () => unawaited(onRetry()));
  } else {
    showInfo(context, message);
  }
}

/// Ask before an irreversible action, and return whether the user agreed.
///
/// This replaces the `AlertDialog` this app used to build at each call site.
/// Two things were wrong with those (`alerts.md`):
///
///  1. The destructive choice was a *filled* button — the same visual weight as
///     the confirming action elsewhere in the app — so the option that loses
///     money or closes an account was the most inviting thing on screen. Apple
///     marks it `isDestructive`, which renders it red and unemphasised.
///  2. Material's dialog puts the buttons side by side with no platform
///     default, so each site had invented its own order. `CupertinoAlertDialog`
///     stacks them and places the cancel action last with `isDefaultAction`,
///     which is the iOS convention: the safe way out is the one under your
///     thumb.
///
/// [title] is a question ("取消已接的訂單？") and [message] says what the
/// consequence is — the thing the user cannot undo, not a restatement of the
/// title.
Future<bool> confirmDestructive(
  BuildContext context, {
  required String title,
  required String message,
  required String confirmLabel,
  String cancelLabel = '返回',
}) async {
  final bool? confirmed = await showDialog<bool>(
    context: context,
    builder: (BuildContext context) => CupertinoAlertDialog(
      title: Text(title),
      content: Padding(
        padding: const EdgeInsets.only(top: AppTheme.space2),
        child: Text(message),
      ),
      actions: <Widget>[
        CupertinoDialogAction(
          isDestructiveAction: true,
          onPressed: () => Navigator.of(context).pop(true),
          child: Text(confirmLabel),
        ),
        CupertinoDialogAction(
          isDefaultAction: true,
          onPressed: () => Navigator.of(context).pop(false),
          child: Text(cancelLabel),
        ),
      ],
    ),
  );
  return confirmed ?? false;
}

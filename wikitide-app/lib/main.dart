import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'app_state.dart';
import 'screens/shell.dart';
import 'web_client.dart';

void main() {
  runWebClient();
}

class WikiTideApp extends StatefulWidget {
  const WikiTideApp({super.key, required this.state});
  final AppState state;
  @override
  State<WikiTideApp> createState() => _WikiTideAppState();
}

class _WikiTideAppState extends State<WikiTideApp> {
  late final Future<void> _ready = widget.state.initialize();
  ThemeData _theme(Brightness brightness) {
    final dark = brightness == Brightness.dark;
    final ink = Color(dark ? 0xffeee8f5 : 0xff292439);
    final line = Color(dark ? 0xff393040 : 0xffddd6e9);
    final scheme =
        ColorScheme.fromSeed(
          seedColor: const Color(0xff7057a8),
          brightness: brightness,
        ).copyWith(
          primary: Color(dark ? 0xffbba0e0 : 0xff6c5099),
          onPrimary: Color(dark ? 0xff251633 : 0xffffffff),
          surface: Color(dark ? 0xff221b2d : 0xffffffff),
          onSurface: ink,
          onSurfaceVariant: Color(dark ? 0xffaaa0ba : 0xff6b647a),
          outline: line,
          surfaceContainerLow: Color(dark ? 0xff211a2d : 0xfffbf9fe),
        );
    final stadium = const StadiumBorder();
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      dividerColor: line,
      scaffoldBackgroundColor: Color(dark ? 0xff15131d : 0xfff6f3fc),
      splashFactory: NoSplash.splashFactory,
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        surfaceTintColor: Colors.transparent,
        titleTextStyle: TextStyle(
          fontFamily: 'WikiTide Kai',
          fontSize: 23,
          color: ink,
        ),
        elevation: 0,
        scrolledUnderElevation: 0,
      ),
      cardTheme: CardThemeData(
        elevation: 0,
        color: scheme.surface,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(7),
          side: BorderSide(color: line),
        ),
      ),
      dialogTheme: DialogThemeData(
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(9)),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surface,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: line),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: line),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide(color: scheme.primary),
        ),
        labelStyle: TextStyle(fontSize: 13, color: scheme.onSurfaceVariant),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: stadium,
          padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
          textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w400),
        ),
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          shape: stadium,
          side: BorderSide(color: line),
          textStyle: const TextStyle(fontSize: 13),
        ),
      ),
      textButtonTheme: TextButtonThemeData(
        style: TextButton.styleFrom(
          shape: stadium,
          textStyle: const TextStyle(fontSize: 12),
        ),
      ),
      listTileTheme: ListTileThemeData(
        contentPadding: const EdgeInsets.symmetric(horizontal: 12),
        iconColor: scheme.onSurfaceVariant,
        titleTextStyle: TextStyle(fontSize: 13, color: ink),
        subtitleTextStyle: TextStyle(
          fontSize: 11,
          color: scheme.onSurfaceVariant,
        ),
      ),
      expansionTileTheme: ExpansionTileThemeData(
        shape: const Border(),
        collapsedShape: const Border(),
        tilePadding: const EdgeInsets.symmetric(horizontal: 12),
        iconColor: scheme.onSurfaceVariant,
      ),
      textTheme: ThemeData(
        brightness: brightness,
      ).textTheme.apply(bodyColor: ink, displayColor: ink),
    );
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
    listenable: widget.state,
    builder: (context, _) => MaterialApp(
      key: ValueKey(widget.state.sessionEpoch),
      title: '识汐 WikiTide',
      debugShowCheckedModeBanner: false,
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      themeMode: widget.state.theme,
      locale: const Locale('zh', 'CN'),
      supportedLocales: const [Locale('zh', 'CN'), Locale('en')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      home: FutureBuilder<void>(
        future: _ready,
        builder: (context, snapshot) =>
            snapshot.connectionState == ConnectionState.done
            ? LibraryShell(state: widget.state)
            : const Scaffold(body: Center(child: CircularProgressIndicator())),
      ),
    ),
  );
}

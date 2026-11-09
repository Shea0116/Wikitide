import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';

/// Colors and type roles taken from knowledge-site/static/design.css.
class WikiDesign {
  WikiDesign(BuildContext context)
    : dark = Theme.of(context).brightness == Brightness.dark;
  final bool dark;
  Color get background => Color(dark ? 0xff15131d : 0xfff6f3fc);
  Color get ink => Color(dark ? 0xffeee8f5 : 0xff292439);
  Color get muted => Color(dark ? 0xffaaa0ba : 0xff6b647a);
  Color get line => Color(dark ? 0xff393040 : 0xffddd6e9);
  Color get accent => Color(dark ? 0xffc0a4ed : 0xff7057a8);
  Color get surface => Color(dark ? 0xff221b2d : 0xffffffff);
  Color get hover => Color(dark ? 0xff2c223b : 0xffeee7f8);
  TextStyle kai(double size) => TextStyle(
    fontFamily: 'WikiTide Kai',
    fontSize: size,
    fontWeight: FontWeight.w400,
    color: ink,
  );
  TextStyle mono(double size) => TextStyle(
    fontFamily: 'monospace',
    fontSize: size,
    color: muted,
    letterSpacing: 1.2,
  );
  TextStyle song(double size) => TextStyle(
    fontFamily: defaultTargetPlatform == TargetPlatform.macOS
        ? 'Songti SC'
        : 'WikiTide Song',
    fontFamilyFallback: const ['Songti SC', 'STSong', 'serif'],
    fontSize: size,
    height: 1.28,
    fontWeight: FontWeight.w400,
    color: ink,
    letterSpacing: -1.8,
  );
}

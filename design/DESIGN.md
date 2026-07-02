---
name: TrendCut
colors:
  surface: '#FFFFFF'
  surface-dim: '#dadada'
  surface-bright: '#f9f9f9'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f3f3f3'
  surface-container: '#eeeeee'
  surface-container-high: '#e8e8e8'
  surface-container-highest: '#e2e2e2'
  on-surface: '#1a1c1c'
  on-surface-variant: '#424754'
  inverse-surface: '#2f3131'
  inverse-on-surface: '#f0f1f1'
  outline: '#727785'
  outline-variant: '#c2c6d6'
  surface-tint: '#005ac2'
  primary: '#0058be'
  on-primary: '#ffffff'
  primary-container: '#2170e4'
  on-primary-container: '#fefcff'
  inverse-primary: '#adc6ff'
  secondary: '#006c49'
  on-secondary: '#ffffff'
  secondary-container: '#6cf8bb'
  on-secondary-container: '#00714d'
  tertiary: '#924700'
  on-tertiary: '#ffffff'
  tertiary-container: '#b75b00'
  on-tertiary-container: '#fffbff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#d8e2ff'
  primary-fixed-dim: '#adc6ff'
  on-primary-fixed: '#001a42'
  on-primary-fixed-variant: '#004395'
  secondary-fixed: '#6ffbbe'
  secondary-fixed-dim: '#4edea3'
  on-secondary-fixed: '#002113'
  on-secondary-fixed-variant: '#005236'
  tertiary-fixed: '#ffdcc6'
  tertiary-fixed-dim: '#ffb786'
  on-tertiary-fixed: '#311400'
  on-tertiary-fixed-variant: '#723600'
  background: '#f9f9f9'
  on-background: '#1a1c1c'
  surface-variant: '#e2e2e2'
  border-hairline: '#E5E5E5'
  text-muted: '#737373'
  status-warning: '#F59E0B'
  status-error: '#EF4444'
  data-fill: rgba(59, 130, 246, 0.08)
typography:
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.01em
  data-display:
    fontFamily: JetBrains Mono
    fontSize: 28px
    fontWeight: '500'
    lineHeight: 36px
  body-md:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
  label-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.05em
  label-xs-mono:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 14px
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '700'
    lineHeight: 34px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  container-padding: 24px
  stack-gap: 16px
  section-margin: 40px
  gutter: 20px
---

## Brand & Style
The design system is a "Technical Instrument"—a precise, data-forward, and quiet interface designed for high-agency users who value mathematical honesty over gamification. It draws heavily from **Minimalism** and **Corporate/Modern** aesthetics, specifically echoing the utilitarian elegance of Apple Health and Linear.

The personality is characterized by "Deterministic Calm." It avoids aggressive marketing patterns, instead using vast whitespace, high-fidelity typography, and surgical precision in its data visualization. The emotional response should be one of clarity and control; the UI recedes to allow the data's "signal" to emerge from the "noise."

## Colors
The palette is hyper-restrained to maintain a clinical, premium feel. The primary background is a very light grey (`#FAFAFA`) to allow white surface cards to pop with subtle depth. 

- **Primary Blue (`#3B82F6`)**: Reserved for the EWMA trend line, active interactive states, and primary data signals.
- **Secondary Green (`#10B981`)**: Used exclusively for positive "on-track" status indicators and weight-loss success metrics.
- **Neutral/Surface**: White containers with hairline borders create a "sheet" metaphor common in high-end mobile OS environments.
- **Data Uncertainty**: Use low-opacity fills (`data-fill`) to represent confidence intervals and statistical ranges without cluttering the visual field.

## Typography
The system uses a dual-type approach. **Inter** handles all narrative and structural hierarchy, using tight letter-spacing and bold weights for large metrics to emulate the iOS "Fitness" look. 

**JetBrains Mono** is introduced for technical readouts, TDEE values, and chart axes. This monospaced inclusion reinforces the "lab instrument" aesthetic and ensures that fluctuating numbers don't cause horizontal layout shifts. Labels should be kept small, often in all-caps with generous tracking, and rendered in `text-muted` to keep the focus on the primary data.

## Layout & Spacing
The layout follows a **Fixed Grid** philosophy for desktop (centering content within a maximum width of 1200px) and a **Fluid Grid** for mobile. 

- **Padding**: Elements are given generous internal padding (`24px`) to feel premium and airy.
- **Rhythm**: Use a 4px/8px baseline shift for minor adjustments, but maintain large `40px` margins between major sections (e.g., between the Weight Trend Chart and the TDEE Analysis).
- **Responsive Behavior**: On mobile, the charts should maintain a fixed aspect ratio (e.g., 4:3) and consume the full width of the screen minus the `container-padding`. On desktop, data cards should reflow into a multi-column grid.

## Elevation & Depth
Depth is conveyed through **Tonal Layers** and **Low-Contrast Outlines** rather than heavy shadows. 

Surfaces sit on the `#FAFAFA` background. Each card uses a white fill and a `1px` solid border of `#E5E5E5`. For a more "iOS-like" premium feel, a very soft, diffused shadow (0px 4px 20px rgba(0,0,0,0.04)) may be applied to the primary dashboard cards to distinguish them from the background. Backdrop blurs (Glassmorphism) are reserved specifically for sticky navigation headers or floating action buttons to maintain context of the underlying data.

## Shapes
The shape language is "Apple-standard" rounded. All primary containers and cards use a `16px` to `20px` corner radius. Buttons and input fields should follow suit with `rounded-lg` (8px) or `rounded-xl` (12px) to maintain a soft, approachable feel despite the technical nature of the data. Charts should avoid sharp corners; trend lines should use monotonic cubic interpolation for smooth, organic paths.

## Components
- **Cards**: Large-radius (`20px`) containers with `24px` padding. Headers within cards should use `label-sm` muted text.
- **Charts**: Use `3px` stroke width for the primary trend line. Raw data points should be small, 4px diameter circles with 40% opacity to minimize visual noise. Use a light vertical grid line only for major time intervals (e.g., weeks).
- **List Rows**: iOS-style "Settings" rows with a white background, hairline bottom borders, and a chevron for drill-down.
- **Inputs**: Minimalist fields with no background (transparent) and a simple bottom border that highlights in Blue (`#3B82F6`) on focus.
- **Status Chips**: Small, pill-shaped indicators with low-saturation backgrounds and high-saturation text for "On Track" or "Stalled" states.
- **Data Metrics**: Large-scale bold numbers using `headline-lg` paired with a small `label-xs-mono` unit suffix (e.g., "kg" or "kcal").
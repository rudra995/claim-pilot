/** @type {import('tailwindcss').Config} */
// Design tokens. Green brand (accent) on a soft grey canvas, generous rounding, pill controls.
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', 'Inter', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      colors: {
        canvas: '#f2f4f3',
        ink: {
          DEFAULT: '#14201c',
          soft: '#34423d',
          muted: '#6b7873',
          faint: '#9aa5a0',
        },
        line: '#e4e8e6',
        accent: {
          50: '#ebf6f1',
          100: '#d2ecdf',
          200: '#a6d8c0',
          300: '#71bd9c',
          400: '#3f9f78',
          500: '#22845f',
          600: '#1a6f50',
          700: '#155a41',
          800: '#114735',
          900: '#0c3528',
        },
      },
      borderRadius: {
        md: '0.625rem',
        lg: '0.875rem',
        xl: '1.375rem',
        '2xl': '1.75rem',
      },
      boxShadow: {
        card: '0 1px 2px rgb(20 32 28 / 0.04), 0 1px 1px rgb(20 32 28 / 0.02)',
        lift: '0 12px 32px -12px rgb(20 32 28 / 0.18), 0 2px 6px rgb(20 32 28 / 0.04)',
        pop: '0 24px 64px -16px rgb(20 32 28 / 0.30)',
        glow: '0 8px 24px -8px rgb(26 111 80 / 0.55)',
      },
      keyframes: {
        'fade-up': { from: { opacity: 0, transform: 'translateY(8px)' }, to: { opacity: 1, transform: 'none' } },
        'fade-in': { from: { opacity: 0 }, to: { opacity: 1 } },
        'scale-in': { from: { opacity: 0, transform: 'scale(0.96)' }, to: { opacity: 1, transform: 'none' } },
        'slide-in': { from: { opacity: 0, transform: 'translateX(24px)' }, to: { opacity: 1, transform: 'none' } },
        'slide-up': { from: { opacity: 0, transform: 'translateY(24px) scale(0.98)' }, to: { opacity: 1, transform: 'none' } },
        grow: { from: { transform: 'scaleY(0)' }, to: { transform: 'scaleY(1)' } },
        'grow-x': { from: { transform: 'scaleX(0)' }, to: { transform: 'scaleX(1)' } },
        shimmer: { from: { backgroundPosition: '200% 0' }, to: { backgroundPosition: '-200% 0' } },
        breathe: { '0%, 100%': { opacity: 1 }, '50%': { opacity: 0.35 } },
        ring: { '0%': { boxShadow: '0 0 0 0 rgb(34 132 95 / 0.35)' }, '100%': { boxShadow: '0 0 0 10px rgb(34 132 95 / 0)' } },
        dots: { '0%, 80%, 100%': { opacity: 0.25, transform: 'translateY(0)' }, '40%': { opacity: 1, transform: 'translateY(-2px)' } },
      },
      animation: {
        'fade-up': 'fade-up 420ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'fade-in': 'fade-in 240ms ease-out both',
        'scale-in': 'scale-in 240ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'slide-in': 'slide-in 320ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'slide-up': 'slide-up 320ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        grow: 'grow 700ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        'grow-x': 'grow-x 700ms cubic-bezier(0.2, 0.7, 0.2, 1) both',
        shimmer: 'shimmer 1.6s linear infinite',
        breathe: 'breathe 1.6s ease-in-out infinite',
        ring: 'ring 1.4s ease-out infinite',
        dots: 'dots 1.2s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}

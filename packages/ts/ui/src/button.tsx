import { Slot } from "@radix-ui/react-slot";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  asChild?: boolean;
  children: ReactNode;
  variant?: "primary" | "secondary" | "quiet";
}

export function Button({
  asChild,
  children,
  className = "",
  variant = "primary",
  ...props
}: ButtonProps) {
  const Component = asChild ? Slot : "button";
  return (
    <Component
      className={`ha-button ha-button--${variant} ${className}`}
      {...props}
    >
      {children}
    </Component>
  );
}

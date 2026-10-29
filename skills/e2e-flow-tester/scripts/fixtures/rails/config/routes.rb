Rails.application.routes.draw do
  devise_for :users
  root "home#index"
  get "login", to: "sessions#new"
  resources :projects, only: [:index, :new, :create, :destroy] do
    member do
      post :archive
    end
  end
  namespace :admin do
    resources :invoices, only: [:index]
  end
  get "status", to: "pages#status"
end

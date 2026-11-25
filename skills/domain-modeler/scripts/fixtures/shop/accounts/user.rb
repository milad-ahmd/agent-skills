class User < ApplicationRecord
  has_many :adresses
end

class Accounts::Adress < ApplicationRecord
end
